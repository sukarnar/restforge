"""Structured audit log (JSON lines). Never logs request bodies or credentials."""
from __future__ import annotations

import json
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path


class AuditLogger:
    def __init__(self, path: str | None):
        self.logger = logging.getLogger("restforge.audit")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        if path and not self.logger.handlers:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(path, maxBytes=10_000_000, backupCount=5, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            self.logger.addHandler(handler)

    def record(self, **event) -> None:
        if not self.logger.handlers:
            return
        event.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self.logger.info(json.dumps(event, default=str))
