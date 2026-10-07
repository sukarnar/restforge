"""restforge CLI – create and manage REST endpoints, credentials and the server."""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer
import yaml

from .config import (DEFAULT_CONFIG, ApiKeyRecord, ConfigError, EndpointSpec, ParamSpec,
                     ProjectConfig, SourceSpec, UserRecord)
from .security.hashing import generate_api_key, hash_api_key, hash_password

app = typer.Typer(help="restforge – declarative, secure REST endpoints for any data source.",
                  no_args_is_help=True)
source_app = typer.Typer(help="Manage data sources.", no_args_is_help=True)
endpoint_app = typer.Typer(help="Manage REST endpoints.", no_args_is_help=True)
key_app = typer.Typer(help="Manage API keys.", no_args_is_help=True)
user_app = typer.Typer(help="Manage users (for JWT login).", no_args_is_help=True)
app.add_typer(source_app, name="source")
app.add_typer(endpoint_app, name="endpoint")
app.add_typer(key_app, name="key")
app.add_typer(user_app, name="user")

CONFIG_OPT = typer.Option(DEFAULT_CONFIG, "--config", "-c", help="Project config file.")


# ------------------------------------------------------------------ helpers
def _load(path: str) -> ProjectConfig:
    try:
        return ProjectConfig.load(path)
    except ConfigError as exc:
        typer.secho(f"✗ {exc}", fg="red", err=True)
        raise typer.Exit(1)


def _save(cfg: ProjectConfig, path: str) -> None:
    try:
        # re-validate the whole document before writing
        ProjectConfig.model_validate(cfg.model_dump(mode="json", by_alias=True))
    except Exception as exc:
        typer.secho(f"✗ {exc}", fg="red", err=True)
        raise typer.Exit(1)
    cfg.save(path)


def _ok(msg: str) -> None:
    typer.secho(f"✓ {msg}", fg="green")


def load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines). Existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _kv(pairs: list[str]) -> dict[str, str]:
    out = {}
    for p in pairs:
        if "=" not in p:
            raise typer.BadParameter(f"expected KEY=VALUE, got '{p}'")
        k, v = p.split("=", 1)
        out[k.strip()] = v.strip()
    return out


# ------------------------------------------------------------------- init
@app.command()
def init(name: str = typer.Option("restforge-project", help="Project name."),
         directory: Path = typer.Argument(Path("."), help="Target directory.")):
    """Create a new project (restforge.yaml, .env with a generated JWT secret, .gitignore)."""
    directory.mkdir(parents=True, exist_ok=True)
    cfg_path = directory / DEFAULT_CONFIG
    if cfg_path.exists():
        typer.secho(f"✗ {cfg_path} already exists", fg="red", err=True)
        raise typer.Exit(1)
    ProjectConfig(project=name).save(cfg_path)
    env = directory / ".env"
    if not env.exists():
        env.write_text(f"# Secrets – never commit this file\nRESTFORGE_JWT_SECRET={secrets.token_urlsafe(48)}\n",
                       encoding="utf-8")
        try:
            os.chmod(env, 0o600)
        except OSError:
            pass
    gi = directory / ".gitignore"
    if not gi.exists():
        gi.write_text(".env\nlogs/\n__pycache__/\n", encoding="utf-8")
    for d in ("data", "handlers", "logs"):
        (directory / d).mkdir(exist_ok=True)
    _ok(f"Initialised project '{name}' in {directory.resolve()}")
    typer.echo("Next: restforge source add …  →  restforge endpoint add …  →  restforge key create …  →  restforge serve")


# ----------------------------------------------------------------- sources
@source_app.command("add")
def source_add(
    name: str,
    type: str = typer.Option(..., "--type", "-t", help="sql | file | rest | callable"),
    url: Optional[str] = typer.Option(None, help="SQLAlchemy URL – use ${ENV:VAR} for credentials."),
    path: Optional[str] = typer.Option(None, help="File path relative to data_root."),
    sheet: Optional[str] = typer.Option(None, help="Excel sheet name."),
    base_url: Optional[str] = typer.Option(None, help="Upstream REST base URL."),
    header: list[str] = typer.Option([], help="Upstream header KEY=VALUE (repeatable)."),
    module: Optional[str] = typer.Option(None, help="Default module for callable targets."),
    read_write: bool = typer.Option(False, "--read-write", help="Allow writes on a SQL source."),
    allow_host: bool = typer.Option(False, "--allow-host", help="Add the REST host to the allow-list."),
    description: str = "",
    config: str = CONFIG_OPT,
):
    """Register a data source."""
    cfg = _load(config)
    if name in cfg.sources:
        typer.secho(f"✗ source '{name}' already exists", fg="red", err=True)
        raise typer.Exit(1)
    if url and "://" in url and "@" in url and "${ENV:" not in url:
        typer.secho("⚠ The URL appears to contain inline credentials. Prefer ${ENV:DB_URL}.", fg="yellow")
    try:
        spec = SourceSpec(type=type, url=url, path=path, sheet=sheet, base_url=base_url,
                          headers=_kv(header), module=module, read_only=not read_write,
                          description=description)
    except Exception as exc:
        typer.secho(f"✗ {exc}", fg="red", err=True)
        raise typer.Exit(1)
    cfg.sources[name] = spec
    if type == "callable" and module:
        root_mod = module.split(".")[0]
        if root_mod not in cfg.security.allowed_callable_modules:
            cfg.security.allowed_callable_modules.append(root_mod)
    if type == "rest" and allow_host:
        from urllib.parse import urlsplit
        host = urlsplit(base_url).hostname
        if host and host not in cfg.security.allowed_upstream_hosts:
            cfg.security.allowed_upstream_hosts.append(host)
    _save(cfg, config)
    _ok(f"Source '{name}' ({type}) added")


@source_app.command("list")
def source_list(config: str = CONFIG_OPT):
    cfg = _load(config)
    for name, s in cfg.sources.items():
        detail = s.url or s.path or s.base_url or s.module or ""
        ro = " [read-only]" if s.type == "sql" and s.read_only else ""
        typer.echo(f"{name:<20} {s.type:<9} {detail}{ro}")


@source_app.command("remove")
def source_remove(name: str, config: str = CONFIG_OPT):
    cfg = _load(config)
    if any(e.source == name for e in cfg.endpoints):
        typer.secho("✗ endpoints still use this source; remove them first", fg="red", err=True)
        raise typer.Exit(1)
    cfg.sources.pop(name, None)
    _save(cfg, config)
    _ok(f"Source '{name}' removed")


# --------------------------------------------------------------- endpoints
@endpoint_app.command("add")
def endpoint_add(
    name: str,
    source: str = typer.Option(..., "--source", "-s"),
    path: str = typer.Option(..., "--path", "-p", help="e.g. /employees/{id}"),
    method: str = typer.Option("GET", "--method", "-m"),
    param: list[str] = typer.Option([], "--param", help="name:type[:query|path|body][:required] (repeatable)"),
    scope: list[str] = typer.Option([], "--scope", help="Required scope (repeatable)."),
    public: bool = typer.Option(False, "--public", help="No authentication (use with care)."),
    rate_limit: Optional[int] = typer.Option(None, help="Requests per window for this endpoint."),
    description: str = "",
    # sql
    query: Optional[str] = typer.Option(None, help="SQL with :named binds."),
    operation: Optional[str] = typer.Option(None, help="list|get|create|update|delete (table mode)"),
    table: Optional[str] = None,
    key: Optional[str] = None,
    columns: Optional[str] = typer.Option(None, help="Comma-separated column allow-list."),
    # file
    filter: list[str] = typer.Option([], "--filter", help="Filterable field (repeatable)."),
    # rest
    upstream_path: Optional[str] = None,
    upstream_method: Optional[str] = None,
    # callable
    target: Optional[str] = typer.Option(None, help="module:function"),
    max_limit: int = 500,
    config: str = CONFIG_OPT,
):
    """Create an endpoint."""
    cfg = _load(config)
    if cfg.endpoint(name):
        typer.secho(f"✗ endpoint '{name}' already exists", fg="red", err=True)
        raise typer.Exit(1)
    if public and scope:
        typer.secho("✗ --public and --scope are mutually exclusive", fg="red", err=True)
        raise typer.Exit(1)
    try:
        params = [ParamSpec.parse_short(p) for p in param]
        for f in filter:   # filters are implicit optional query params
            if not any(p.name == f for p in params):
                params.append(ParamSpec(name=f))
        ep = EndpointSpec(
            name=name, source=source, path=path, method=method.upper(), params=params,
            scopes=scope, public=public, rate_limit=rate_limit, description=description,
            query=query, operation=operation or ("query" if query else None), table=table, key=key,
            columns=[c.strip() for c in columns.split(",")] if columns else None,
            filters=filter, upstream_path=upstream_path,
            upstream_method=upstream_method.upper() if upstream_method else None,
            target=target, max_limit=max_limit,
        )
    except Exception as exc:
        typer.secho(f"✗ {exc}", fg="red", err=True)
        raise typer.Exit(1)
    cfg.endpoints.append(ep)
    _save(cfg, config)
    if public:
        typer.secho(f"⚠ '{name}' is PUBLIC – anyone can call it.", fg="yellow")
    _ok(f"Endpoint '{name}': {ep.method} {cfg.server.base_path}{ep.path}")


@endpoint_app.command("crud")
def endpoint_crud(
    resource: str = typer.Argument(..., help="Resource name, e.g. employees"),
    source: str = typer.Option(..., "--source", "-s"),
    table: str = typer.Option(...),
    key: str = typer.Option(..., help="Primary key column."),
    key_type: str = typer.Option("integer"),
    field: list[str] = typer.Option([], "--field", help="Writable column name:type (repeatable)."),
    filter: list[str] = typer.Option([], "--filter", help="Filterable column on list (repeatable)."),
    read_scope: str = typer.Option(None, help="Scope for reads (default <resource>:read)."),
    write_scope: str = typer.Option(None, help="Scope for writes (default <resource>:write)."),
    read_only: bool = typer.Option(False, "--read-only", help="Generate list+get only."),
    config: str = CONFIG_OPT,
):
    """Generate list/get/create/update/delete endpoints for a SQL table."""
    cfg = _load(config)
    rs = read_scope or f"{resource}:read"
    ws = write_scope or f"{resource}:write"
    fields = [ParamSpec.parse_short(f) for f in field]
    cols = [key] + [f.name for f in fields] + [f for f in filter if f not in {x.name for x in fields}]
    columns = list(dict.fromkeys(cols)) if fields or filter else None
    kp = ParamSpec(name=key, type=key_type, location="path", required=True)
    body = lambda req: [f.model_copy(update={"location": "body", "required": req and f.required}) for f in fields]
    specs = [
        dict(name=f"{resource}-list", path=f"/{resource}", method="GET", operation="list",
             params=[ParamSpec(name=f) for f in filter], scopes=[rs]),
        dict(name=f"{resource}-get", path=f"/{resource}/{{{key}}}", method="GET", operation="get",
             params=[kp], scopes=[rs]),
    ]
    if not read_only:
        specs += [
            dict(name=f"{resource}-create", path=f"/{resource}", method="POST", operation="create",
                 params=body(True), scopes=[ws]),
            dict(name=f"{resource}-update", path=f"/{resource}/{{{key}}}", method="PATCH",
                 operation="update", params=[kp] + body(False), scopes=[ws]),
            dict(name=f"{resource}-delete", path=f"/{resource}/{{{key}}}", method="DELETE",
                 operation="delete", params=[kp], scopes=[ws]),
        ]
    try:
        for s in specs:
            cfg.endpoints.append(EndpointSpec(source=source, table=table, key=key, columns=columns,
                                              tags=[resource], **s))
    except Exception as exc:
        typer.secho(f"✗ {exc}", fg="red", err=True)
        raise typer.Exit(1)
    _save(cfg, config)
    _ok(f"Generated {len(specs)} endpoints for '{resource}' (scopes: {rs}{'' if read_only else ', ' + ws})")


@endpoint_app.command("list")
def endpoint_list(config: str = CONFIG_OPT):
    cfg = _load(config)
    base = cfg.server.base_path
    for e in cfg.endpoints:
        auth = "PUBLIC" if e.public else (",".join(e.scopes) or "auth")
        typer.echo(f"{e.method:<7} {base + e.path:<36} {e.name:<24} {e.source:<14} {auth}")


@endpoint_app.command("show")
def endpoint_show(name: str, config: str = CONFIG_OPT):
    cfg = _load(config)
    ep = cfg.endpoint(name)
    if not ep:
        raise typer.BadParameter(f"no endpoint '{name}'")
    typer.echo(yaml.safe_dump(ep.model_dump(mode="json", by_alias=True, exclude_none=True), sort_keys=False))


@endpoint_app.command("remove")
def endpoint_remove(name: str, config: str = CONFIG_OPT):
    cfg = _load(config)
    before = len(cfg.endpoints)
    cfg.endpoints = [e for e in cfg.endpoints if e.name != name]
    if len(cfg.endpoints) == before:
        raise typer.BadParameter(f"no endpoint '{name}'")
    _save(cfg, config)
    _ok(f"Endpoint '{name}' removed")


# ------------------------------------------------------------------- keys
@key_app.command("create")
def key_create(name: str, scope: list[str] = typer.Option([], "--scope"),
               expires_days: Optional[int] = typer.Option(90, help="0 = never expires."),
               config: str = CONFIG_OPT):
    """Create an API key. The plaintext key is printed ONCE."""
    cfg = _load(config)
    key_id, plaintext = generate_api_key()
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(days=expires_days)).isoformat() if expires_days else None
    cfg.security.api_keys.append(ApiKeyRecord(id=key_id, name=name, hash=hash_api_key(plaintext),
                                              scopes=scope, created=now.isoformat(), expires=expires))
    _save(cfg, config)
    _ok(f"API key '{name}' created (id {key_id}, scopes: {', '.join(scope) or 'none'})")
    typer.secho(f"\n  {plaintext}\n", bold=True)
    typer.echo("Store it now – it cannot be shown again. Send it in the "
               f"'{cfg.security.api_key_header}' header.")


@key_app.command("list")
def key_list(config: str = CONFIG_OPT):
    cfg = _load(config)
    for k in cfg.security.api_keys:
        state = "revoked" if k.disabled else "active"
        typer.echo(f"{k.id}  {k.name:<20} {state:<8} expires={k.expires or 'never'}  scopes={','.join(k.scopes)}")


@key_app.command("revoke")
def key_revoke(key_id: str, config: str = CONFIG_OPT):
    cfg = _load(config)
    for k in cfg.security.api_keys:
        if k.id == key_id:
            k.disabled = True
            _save(cfg, config)
            _ok(f"Key {key_id} revoked (restart/reload the server to apply)")
            return
    raise typer.BadParameter(f"no key '{key_id}'")


# ------------------------------------------------------------------ users
@user_app.command("add")
def user_add(username: str, scope: list[str] = typer.Option([], "--scope"),
             password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
             config: str = CONFIG_OPT):
    """Add a user who can obtain JWTs via POST /auth/token."""
    if len(password) < 12:
        raise typer.BadParameter("password must be at least 12 characters")
    cfg = _load(config)
    if any(u.username == username for u in cfg.security.users):
        raise typer.BadParameter(f"user '{username}' exists")
    cfg.security.users.append(UserRecord(username=username, password_hash=hash_password(password), scopes=scope))
    _save(cfg, config)
    _ok(f"User '{username}' added")


@user_app.command("remove")
def user_remove(username: str, config: str = CONFIG_OPT):
    cfg = _load(config)
    cfg.security.users = [u for u in cfg.security.users if u.username != username]
    _save(cfg, config)
    _ok(f"User '{username}' removed")


# --------------------------------------------------------- validate/serve
@app.command()
def validate(config: str = CONFIG_OPT):
    """Validate the project (schema, adapters, security rules) without starting the server."""
    load_dotenv(Path(config).resolve().parent / ".env")
    from .server import create_app
    try:
        create_app(config)
    except Exception as exc:
        typer.secho(f"✗ {exc}", fg="red", err=True)
        raise typer.Exit(1)
    cfg = _load(config)
    public = [e.name for e in cfg.endpoints if e.public]
    _ok(f"{len(cfg.endpoints)} endpoints across {len(cfg.sources)} sources are valid")
    if public:
        typer.secho(f"⚠ public endpoints: {', '.join(public)}", fg="yellow")


@app.command()
def serve(config: str = CONFIG_OPT,
          host: Optional[str] = typer.Option(None, help="Default from config (127.0.0.1)."),
          port: Optional[int] = None,
          reload: bool = typer.Option(False, help="Auto-reload on config/code changes (dev only)."),
          workers: int = 1):
    """Run the API server."""
    import uvicorn
    cfg_path = Path(config).resolve()
    load_dotenv(cfg_path.parent / ".env")
    cfg = _load(str(cfg_path))
    os.environ["RESTFORGE_CONFIG"] = str(cfg_path)
    os.chdir(cfg_path.parent)
    h = host or cfg.server.host
    if h in ("0.0.0.0", "::") and not cfg.server.ssl_certfile:
        typer.secho("⚠ Listening on all interfaces without TLS – put a TLS proxy in front.", fg="yellow")
    uvicorn.run("restforge.server:app_from_env", factory=True, host=h, port=port or cfg.server.port,
                reload=reload, reload_includes=["*.yaml", "*.py"] if reload else None,
                workers=None if reload else workers, server_header=False, proxy_headers=True,
                ssl_certfile=cfg.server.ssl_certfile, ssl_keyfile=cfg.server.ssl_keyfile)


if __name__ == "__main__":
    app()
