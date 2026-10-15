"""LoadRunner (Template Method): lock -> extract -> validate -> map -> load -> record."""
from __future__ import annotations

import csv
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anyio
from sqlalchemy import Boolean, Date, DateTime, Float, Integer, Numeric, String, inspect as sa_inspect
from sqlalchemy.exc import SQLAlchemyError

from ..config import ColumnMap, LoadSpec, ProjectConfig
from ..sources.base import DataSource, ExecutionContext, SourceError
from .mapping import RowError, RowMapper
from .writers import make_writer

log = logging.getLogger("restforge.loads")
LOCK_STALE_SECONDS = 6 * 3600


class LoadError(Exception):
    pass


@dataclass
class LoadReport:
    run_id: str
    load: str
    mode: str
    status: str = "running"            # success | partial | failed | dry_run
    read: int = 0
    mapped: int = 0
    rejected: int = 0
    inserted: int = 0
    updated: int = 0
    upserted: int = 0
    deleted: int = 0
    started: str = ""
    finished: str = ""
    seconds: float = 0.0
    rejects_file: str | None = None
    error: str | None = None
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    preview: list[dict] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if self.status == "failed":
            return 1
        return 2 if self.rejected else 0

    def to_dict(self) -> dict:
        return asdict(self)


def column_type_name(col) -> str:
    """restforge mapping type that fits a reflected SQLAlchemy column."""
    t = col.type
    if isinstance(t, Boolean):
        return "boolean"
    if isinstance(t, Integer):
        return "integer"
    if isinstance(t, (Numeric, Float)):
        # Oracle NUMBER(p,0) reflects as Numeric with scale 0
        return "integer" if getattr(t, "scale", None) == 0 else "number"
    if isinstance(t, DateTime):
        return "timestamp"
    if isinstance(t, Date):
        return "date"
    return "string"


class LoadRunner:
    def __init__(self, config: ProjectConfig, sources: dict[str, DataSource], project_root: str | Path,
                 audit=None):
        self.config = config
        self.sources = sources
        self.root = Path(project_root)
        self.audit = audit
        self.dir = self.root / "logs" / "loads"

    # ---------------------------------------------------------------- extract
    async def read_rows(self, spec: LoadSpec) -> list[dict[str, Any]]:
        f = spec.from_
        if f.source:
            src = self.sources[f.source]
            if src.type_name != "file":
                raise LoadError(f"'from.source' must be a file source; use 'from.endpoint' for '{src.type_name}'")
            return list(await anyio.to_thread.run_sync(src._load))

        ep = self.config.endpoint(f.endpoint)
        if ep.method != "GET":
            raise LoadError(f"endpoint '{ep.name}' is {ep.method}; loads read GET endpoints only")
        from ..builder import build_param_model
        unknown = set(f.params) - {p.name for p in ep.params}
        if unknown:
            raise LoadError(f"endpoint '{ep.name}' has no params {sorted(unknown)}")
        try:
            params = build_param_model(ep).model_validate(dict(f.params)).model_dump()
        except Exception as exc:
            raise LoadError(f"invalid params for endpoint '{ep.name}': {exc}") from None
        src = self.sources[ep.source]
        rows: list[dict[str, Any]] = []
        offset, limit = 0, ep.max_limit
        while True:
            data = await src.execute(ExecutionContext(ep, params, None, limit, offset, "load"))
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                rows.extend(data["items"])
                if len(data["items"]) < limit:
                    break
                offset += limit
            elif isinstance(data, list):
                rows.extend(data)
                break
            elif isinstance(data, dict):
                rows.append(data)
                break
            else:
                raise LoadError(f"endpoint '{ep.name}' did not return rows")
        return rows

    # --------------------------------------------------------------- validate
    def _target(self, spec: LoadSpec):
        src = self.sources.get(spec.to.source)
        if src is None or src.type_name != "sql":
            raise LoadError(f"target '{spec.to.source}' is not a SQL source")
        return src

    def inspect_target(self, spec: LoadSpec) -> tuple[Any, dict[str, str], list[str], list[str], list[ColumnMap]]:
        """Return (table, mapping-target -> real column name, problems, warnings, effective columns)."""
        problems: list[str] = []
        warnings: list[str] = []
        src = self._target(spec)
        if src.spec.read_only:
            problems.append(f"target source '{spec.to.source}' is read-only; add it with --read-write "
                            f"or set read_only: false")
        try:
            table = src._table(spec.to.table)
        except SourceError as exc:
            raise LoadError(str(exc)) from None
        real = {c.name.lower(): c.name for c in table.columns}
        names: dict[str, str] = {}
        effective: list[ColumnMap] = []
        for cm in spec.columns:
            col_name = real.get(cm.target.lower())
            if col_name is None:
                problems.append(f"target column '{cm.target}' does not exist in {spec.to.table}")
                continue
            names[cm.target] = col_name
            col = table.c[col_name]
            if cm.review:
                problems.append(f"auto-mapped '{cm.source}' -> '{cm.target}' needs review "
                                f"(confirm with: restforge load map {spec.name} --set \"{cm.source}={cm.target}\")")
            # type compatibility
            want = column_type_name(col)
            if cm.value is None and cm.type == "string" and want in ("integer", "number", "date", "timestamp"):
                warnings.append(f"'{cm.target}' is {col.type} but mapped as string; set type: {want}")
            if cm.type == "number" and want == "integer":
                warnings.append(f"'{cm.target}' is an integer column; decimals would be rejected by the DB")
            # length: enforce the column's limit so overflows become clean rejects
            length = getattr(col.type, "length", None)
            eff = cm
            if isinstance(col.type, String) and length and cm.type == "string":
                if cm.max_length is None or cm.max_length > length:
                    eff = cm.model_copy(update={"max_length": length})
            effective.append(eff)
        mapped = {n.lower() for n in names.values()}
        pk = {c.name for c in table.primary_key.columns}
        for col in table.columns:
            if col.name.lower() in mapped or col.nullable or col.server_default is not None:
                continue
            if getattr(col, "identity", None) is not None or col.computed is not None:
                continue
            if col.name in pk and len(pk) == 1 and isinstance(col.type, Integer):
                warnings.append(f"'{col.name}' (primary key) is not mapped; assuming the database generates it")
                continue
            problems.append(f"required column '{col.name}' (NOT NULL, no default) is not mapped")
        if spec.mode == "upsert":
            keys = {names.get(k, k).lower() for k in spec.keys}
            uniques = [{c.lower() for c in pk}] if pk else []
            try:
                insp = sa_inspect(src.engine)
                schema, _, tbl = spec.to.table.rpartition(".")
                for uc in insp.get_unique_constraints(tbl, schema=schema or src._default_schema):
                    uniques.append({c.lower() for c in uc["column_names"]})
                for ix in insp.get_indexes(tbl, schema=schema or src._default_schema):
                    if ix.get("unique"):
                        uniques.append({c.lower() for c in ix["column_names"] if c})
            except Exception:  # pragma: no cover - some dialects lack these
                pass
            if keys not in uniques:
                msg = (f"upsert keys {sorted(spec.keys)} are not a primary key or unique constraint "
                       f"on {spec.to.table}")
                if src.engine.dialect.name in ("sqlite", "postgresql", "mysql", "mariadb"):
                    problems.append(msg + " (required by this database for upsert)")
                else:
                    warnings.append(msg + "; duplicates in the table could be updated together")
        return table, names, problems, warnings, effective

    @staticmethod
    def check_source_columns(spec: LoadSpec, rows: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
        problems, warnings = [], []
        if not rows:
            warnings.append("source returned no rows")
            return problems, warnings
        header = list(rows[0].keys())
        used = {c.source for c in spec.columns if c.source is not None}
        missing = sorted(used - set(header))
        if missing:
            problems.append(f"source columns not found: {missing}; available: {header}")
        extra = [h for h in header if h not in used]
        if extra:
            (problems if spec.unmapped_source_columns == "error" else warnings).append(
                f"unmapped source columns {'not allowed' if spec.unmapped_source_columns == 'error' else 'ignored'}: {extra}")
        return problems, warnings

    async def validate(self, spec: LoadSpec) -> tuple[list[str], list[str], list[dict]]:
        rows = await self.read_rows(spec)
        _, _, p1, w1, _ = await anyio.to_thread.run_sync(self.inspect_target, spec)
        p2, w2 = self.check_source_columns(spec, rows)
        return p1 + p2, w1 + w2, rows

    # -------------------------------------------------------------------- run
    def _lock(self, name: str) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        lock = self.dir / f"{name}.lock"
        if lock.exists() and time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
            lock.unlink(missing_ok=True)
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise LoadError(f"load '{name}' is already running (lock {lock}); "
                            f"delete the lock file if no run is active") from None
        os.write(fd, f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}".encode())
        os.close(fd)
        return lock

    def _write_rejects(self, report: LoadReport, rejects: list[tuple[int, str, dict]]) -> None:
        if not rejects:
            return
        path = self.dir / f"{report.run_id}_rejects.csv"
        cols: list[str] = []
        for _, _, row in rejects:
            cols += [k for k in row if k not in cols]
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["record", "reason", *cols])
            for n, reason, row in rejects:
                w.writerow([n, reason, *[row.get(c, "") for c in cols]])
        report.rejects_file = str(path)

    def _record(self, report: LoadReport) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        entry = {k: v for k, v in report.to_dict().items() if k != "preview"}
        with (self.dir / "history.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
        if self.audit:
            self.audit.record(event="load", load=report.load, run_id=report.run_id, status=report.status,
                              read=report.read, rejected=report.rejected,
                              written=report.inserted + report.updated + report.upserted)

    async def run(self, spec: LoadSpec, dry_run: bool = False, preview: int = 0) -> LoadReport:
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        report = LoadReport(run_id=run_id, load=spec.name, mode=spec.mode,
                            started=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        t0 = time.perf_counter()
        lock = None
        try:
            if not dry_run:
                lock = self._lock(spec.name)
            # 1. extract
            rows = await self.read_rows(spec)
            report.read = len(rows)
            # 2. validate mapping against source header and target table
            table, names, p1, w1, effective = await anyio.to_thread.run_sync(self.inspect_target, spec)
            p2, w2 = self.check_source_columns(spec, rows)
            report.problems, report.warnings = p1 + p2, w1 + w2
            if report.problems:
                raise LoadError("mapping is not valid:\n  - " + "\n  - ".join(report.problems))
            # 3. map every row first – nothing is written if there are too many rejects
            mapper = RowMapper(spec.model_copy(update={"columns": effective}), run_id)
            good: list[dict[str, Any]] = []
            rejects: list[tuple[int, str, dict]] = []
            for i, row in enumerate(rows, start=1):
                try:
                    mapped = mapper.map_row(row)
                    good.append({names[k]: v for k, v in mapped.items()})
                    if len(report.preview) < preview:
                        report.preview.append({"record": i, "source": row, "target": mapped})
                except RowError as exc:
                    rejects.append((i, str(exc), row))
            report.mapped, report.rejected = len(good), len(rejects)
            self.dir.mkdir(parents=True, exist_ok=True)
            self._write_rejects(report, rejects)
            if report.rejected > spec.max_rejects:
                raise LoadError(f"{report.rejected} row(s) rejected (max_rejects = {spec.max_rejects}); "
                                f"nothing was written. See {report.rejects_file}")
            if dry_run:
                report.status = "dry_run"
                return report
            # 4. load – one transaction for the whole run
            keys = [names[k] if k in names else k for k in spec.keys]
            target = self._target(spec)

            def _write() -> dict:
                target._refresh_if_rotated()
                with target.engine.begin() as conn:
                    writer = make_writer(spec.mode, conn, table, keys)
                    writer.start()
                    for b in range(0, len(good), spec.batch_size):
                        writer.write(good[b:b + spec.batch_size])
                    return writer.counts

            try:
                counts = await anyio.to_thread.run_sync(_write)
            except SQLAlchemyError as exc:
                orig = getattr(exc, "orig", None) or exc
                first = str(orig).strip().splitlines()[0][:300] if str(orig).strip() else type(orig).__name__
                # one line at error level; the traceback (which contains row values) only at debug level
                log.error("load %s run %s failed: %s", spec.name, run_id, first)
                log.debug("load %s run %s traceback", spec.name, run_id, exc_info=exc)
                raise LoadError(f"database error, all changes rolled back: {first}") from None
            for k, v in counts.items():
                setattr(report, k, v)
            report.status = "partial" if report.rejected else "success"
            return report
        except (LoadError, SourceError) as exc:
            report.status, report.error = "failed", str(exc)
            return report
        finally:
            report.finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
            report.seconds = round(time.perf_counter() - t0, 2)
            if lock:
                lock.unlink(missing_ok=True)
            if not dry_run:
                self._record(report)
