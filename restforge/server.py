"""Application factory: wires config -> adapters -> security -> routes."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import sources as _sources  # noqa: F401 (registers built-in adapters)
from .builder import EndpointBuilder
from .config import DEFAULT_CONFIG, ConfigError, ProjectConfig
from .security.audit import AuditLogger
from .security.auth import Authenticator
from .security.middleware import SecurityMiddleware
from .security.ratelimit import InMemoryRateLimiter, RateLimiter
from .sources.base import create_source

log = logging.getLogger("restforge")


class TokenRequest(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


def create_app(config: ProjectConfig | str | Path = DEFAULT_CONFIG,
               project_root: str | Path | None = None,
               limiter: RateLimiter | None = None) -> FastAPI:
    if not isinstance(config, ProjectConfig):
        cfg_path = Path(config)
        project_root = project_root or cfg_path.resolve().parent
        config = ProjectConfig.load(cfg_path)
    project_root = str(project_root or Path.cwd())
    sec = config.security

    data_sources = {name: create_source(name, spec, sec, project_root)
                    for name, spec in config.sources.items()}
    authenticator = Authenticator(config)
    limiter = limiter or InMemoryRateLimiter()
    audit_path = str(Path(project_root) / sec.audit_log) if sec.audit_log else None
    audit = AuditLogger(audit_path)

    builder = EndpointBuilder(config, data_sources, authenticator, limiter, audit)
    problems = builder.validate()
    if problems:
        raise ConfigError("Invalid endpoint definitions:\n  - " + "\n  - ".join(problems))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for s in data_sources.values():
            await s.startup()
        log.info("restforge '%s' started with %d endpoints", config.project, len(config.endpoints))
        try:
            yield
        finally:
            for s in data_sources.values():
                await s.shutdown()

    base = config.server.base_path.rstrip("/")
    docs = sec.expose_docs
    app = FastAPI(
        title=config.project, version=config.version, lifespan=lifespan,
        docs_url=f"{base}/docs" if docs else None, redoc_url=None,
        openapi_url=f"{base}/openapi.json" if docs else None,
    )
    app.state.config = config
    app.state.sources = data_sources

    # ---------------------------------------------------------- middleware
    if sec.cors_origins:
        if "*" in sec.cors_origins:
            raise ConfigError("Wildcard CORS origin is not allowed; list explicit origins")
        app.add_middleware(CORSMiddleware, allow_origins=sec.cors_origins, allow_credentials=False,
                           allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                           allow_headers=["Authorization", "Content-Type", sec.api_key_header])
    app.add_middleware(SecurityMiddleware, max_body_bytes=sec.max_body_bytes,
                       hsts=bool(config.server.ssl_certfile),
                       docs_paths=(f"{base}/docs", f"{base}/openapi.json") if docs else ())

    # ----------------------------------------------------- system routes
    @app.get("/health", include_in_schema=False)
    async def health():
        return {"status": "ok"}             # liveness: no internal details leaked

    @app.get(f"{base}/_meta/health", tags=["system"])
    async def detailed_health(request: Request):
        authenticator.require(request, ["admin"], public=False)
        return {name: await s.health() for name, s in data_sources.items()}

    @app.get(f"{base}/_meta/endpoints", tags=["system"])
    async def list_endpoints(request: Request):
        authenticator.require(request, ["admin"], public=False)
        return [{"name": e.name, "method": e.method, "path": base + e.path, "source": e.source,
                 "public": e.public, "scopes": e.scopes} for e in config.endpoints]

    if "jwt" in sec.auth_methods:
        @app.post(f"{base}/auth/token", tags=["auth"])
        async def issue_token(body: TokenRequest, request: Request):
            ip = request.client.host if request.client else "unknown"
            rl = sec.rate_limit
            for key in (f"login|ip:{ip}", f"login|user:{body.username}"):
                ok, retry = limiter.hit(key, rl.login_requests, rl.window_seconds)
                if not ok:
                    raise HTTPException(429, "Too many login attempts", headers={"Retry-After": str(retry)})
            result = authenticator.login(body.username, body.password)
            audit.record(event="login", user=body.username, ip=ip)
            return result

    app.include_router(builder.build(), prefix=base)
    return app


def app_from_env() -> FastAPI:
    """Factory used by ``restforge serve`` (supports uvicorn --reload)."""
    return create_app(os.environ.get("RESTFORGE_CONFIG", DEFAULT_CONFIG))
