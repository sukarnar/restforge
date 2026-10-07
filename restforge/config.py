"""Declarative project specification (the single source of truth).

Everything the server exposes is described in ``restforge.yaml``. The CLI edits
this file; the server reads it. Secrets are never written here in plaintext:
use ``${ENV:VAR_NAME}`` references, which are resolved at runtime only.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

DEFAULT_CONFIG = "restforge.yaml"
_ENV_REF = re.compile(r"\$\{ENV:([A-Za-z_][A-Za-z0-9_]*)\}")
_CRED_REF = re.compile(r"\$\{CRED:([a-zA-Z][a-zA-Z0-9_\-]{0,63})\.([a-zA-Z_][a-zA-Z0-9_]{0,63})\}")
_NAME = r"^[a-zA-Z][a-zA-Z0-9_\-]{0,63}$"


class ConfigError(Exception):
    """Raised when the specification is invalid or a secret cannot be resolved."""


def resolve_secrets(value: Any, credentials: Any = None) -> Any:
    """Resolve secret references recursively.

    * ``${ENV:VAR}``          – environment variable
    * ``${CRED:name.field}``  – field of a managed credential (needs a CredentialManager)
    """
    if isinstance(value, str):
        def _env(m: re.Match) -> str:
            name = m.group(1)
            if name not in os.environ:
                raise ConfigError(f"Environment variable '{name}' is not set")
            return os.environ[name]

        def _cred(m: re.Match) -> str:
            if credentials is None:
                raise ConfigError("${CRED:...} reference used but no credential manager is configured")
            return str(credentials.field(m.group(1), m.group(2)))

        return _CRED_REF.sub(_cred, _ENV_REF.sub(_env, value))
    if isinstance(value, dict):
        return {k: resolve_secrets(v, credentials) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_secrets(v, credentials) for v in value]
    return value


def credential_refs(value: Any) -> set[str]:
    """Names of credentials referenced via ``${CRED:name.field}`` inside a value."""
    if isinstance(value, str):
        return {m.group(1) for m in _CRED_REF.finditer(value)}
    if isinstance(value, dict):
        return set().union(*(credential_refs(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(credential_refs(v) for v in value)) if value else set()
    return set()


# --------------------------------------------------------------------------- #
# Parameters
# --------------------------------------------------------------------------- #
ParamType = Literal["string", "integer", "number", "boolean"]
ParamLocation = Literal["query", "path", "body"]


class ParamSpec(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
    type: ParamType = "string"
    location: ParamLocation = Field("query", alias="in")
    required: bool = False
    default: Any = None
    description: str = ""
    # Validation constraints (all optional)
    min: float | None = None
    max: float | None = None
    max_length: int | None = 1024          # bounded by default
    pattern: str | None = None
    enum: list[Any] | None = None

    model_config = {"populate_by_name": True}

    @classmethod
    def parse_short(cls, text: str) -> "ParamSpec":
        """Parse CLI shorthand: ``name:type[:in][:required]``  e.g. ``id:integer:path``."""
        parts = text.split(":")
        data: dict[str, Any] = {"name": parts[0]}
        for p in parts[1:]:
            if p in ("string", "integer", "number", "boolean"):
                data["type"] = p
            elif p in ("query", "path", "body"):
                data["in"] = p
            elif p in ("required", "req"):
                data["required"] = True
            else:
                raise ConfigError(f"Unknown param modifier '{p}' in '{text}'")
        if data.get("in") == "path":
            data["required"] = True
        return cls(**data)


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
class SourceSpec(BaseModel):
    type: Literal["sql", "file", "rest", "callable"]
    description: str = ""
    # managed credential used to connect/authenticate (see `restforge cred`)
    credential: str | None = None
    # callable only: credentials the handler may read via ctx.credentials
    credentials: list[str] = Field(default_factory=list)
    # sql
    url: str | None = None
    read_only: bool = True
    pool_size: int = 5
    # file
    path: str | None = None
    sheet: str | None = None
    # rest
    base_url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = 10.0
    # callable
    module: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "SourceSpec":
        if self.type == "sql" and not (self.url or self.credential):
            raise ValueError("source type 'sql' requires 'url' or 'credential'")
        required = {"file": "path", "rest": "base_url"}
        field = required.get(self.type)
        if field and not getattr(self, field):
            raise ValueError(f"source type '{self.type}' requires '{field}'")
        if self.credentials and self.type != "callable":
            raise ValueError("'credentials' (list) is only for callable sources; use 'credential'")
        return self


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
SqlOperation = Literal["query", "list", "get", "create", "update", "delete"]


class EndpointSpec(BaseModel):
    name: str = Field(pattern=_NAME)
    path: str
    method: HttpMethod = "GET"
    source: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    params: list[ParamSpec] = Field(default_factory=list)

    # --- security ---
    public: bool = False                  # explicit opt-out of auth
    scopes: list[str] = Field(default_factory=list)
    rate_limit: int | None = None         # requests / window, overrides default

    # --- sql ---
    operation: SqlOperation | None = None
    query: str | None = None
    table: str | None = None
    key: str | None = None
    columns: list[str] | None = None      # column allow-list

    # --- file ---
    filters: list[str] = Field(default_factory=list)  # filterable fields

    # --- rest ---
    upstream_path: str | None = None
    upstream_method: HttpMethod | None = None

    # --- callable ---
    target: str | None = None             # "package.module:function"

    # --- pagination for list-like results ---
    max_limit: int = 500

    @field_validator("path")
    @classmethod
    def _path(cls, v: str) -> str:
        if not v.startswith("/"):
            v = "/" + v
        if ".." in v or "//" in v:
            raise ValueError("path must not contain '..' or '//'")
        return v.rstrip("/") or "/"

    @model_validator(mode="after")
    def _path_params_declared(self) -> "EndpointSpec":
        in_path = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", self.path))
        declared = {p.name for p in self.params if p.location == "path"}
        missing = in_path - declared
        if missing:
            # auto-declare as required strings to keep CLI ergonomic
            for name in sorted(missing):
                self.params.append(ParamSpec(name=name, location="path", required=True))
        extra = declared - in_path
        if extra:
            raise ValueError(f"path params {sorted(extra)} not present in path '{self.path}'")
        return self


# --------------------------------------------------------------------------- #
# Security
# --------------------------------------------------------------------------- #
class ApiKeyRecord(BaseModel):
    id: str
    name: str
    hash: str                              # sha256 of the key – plaintext never stored
    scopes: list[str] = Field(default_factory=list)
    created: str
    expires: str | None = None
    disabled: bool = False


class UserRecord(BaseModel):
    username: str = Field(pattern=_NAME)
    password_hash: str                     # scrypt
    scopes: list[str] = Field(default_factory=list)
    disabled: bool = False


class JwtSettings(BaseModel):
    secret: str = "${ENV:RESTFORGE_JWT_SECRET}"
    algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    ttl_minutes: int = 30
    issuer: str = "restforge"
    audience: str = "restforge-api"


class RateLimitSettings(BaseModel):
    requests: int = 120
    window_seconds: int = 60
    login_requests: int = 5               # brute-force protection on /auth/token


class HashiCorpVaultSettings(BaseModel):
    url: str = "${ENV:VAULT_ADDR}"
    token: str = "${ENV:VAULT_TOKEN}"
    mount: str = "secret"                 # KV v2 mount point
    path_prefix: str = "restforge"        # secrets at <mount>/data/<prefix>/<name>
    namespace: str | None = None
    verify_tls: bool = True


class CredentialSettings(BaseModel):
    # Providers are consulted in order; the first that has the credential wins.
    providers: list[Literal["vault", "env", "keyring", "hashicorp"]] = Field(
        default_factory=lambda: ["vault", "env"])
    vault_path: str = ".restforge/credentials.vault"
    master_key_env: str = "RESTFORGE_MASTER_KEY"
    cache_ttl_seconds: int = 300
    keyring_service: str = "restforge"
    hashicorp: HashiCorpVaultSettings = Field(default_factory=HashiCorpVaultSettings)


class SecuritySettings(BaseModel):
    credentials: CredentialSettings = Field(default_factory=CredentialSettings)
    auth_methods: list[Literal["api_key", "jwt"]] = Field(default_factory=lambda: ["api_key", "jwt"])
    api_key_header: str = "X-API-Key"
    jwt: JwtSettings = Field(default_factory=JwtSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    api_keys: list[ApiKeyRecord] = Field(default_factory=list)
    users: list[UserRecord] = Field(default_factory=list)
    allowed_callable_modules: list[str] = Field(default_factory=list)
    allowed_upstream_hosts: list[str] = Field(default_factory=list)
    data_root: str = "data"               # file sources are sandboxed here
    cors_origins: list[str] = Field(default_factory=list)
    max_body_bytes: int = 1_048_576
    expose_docs: bool = True
    audit_log: str | None = "logs/audit.jsonl"


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8080
    base_path: str = "/api"
    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None


class ProjectConfig(BaseModel):
    project: str = "restforge-project"
    version: str = "1.0.0"
    server: ServerSettings = Field(default_factory=ServerSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    sources: dict[str, SourceSpec] = Field(default_factory=dict)
    endpoints: list[EndpointSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _integrity(self) -> "ProjectConfig":
        seen_names: set[str] = set()
        seen_routes: set[tuple[str, str]] = set()
        for ep in self.endpoints:
            if ep.source not in self.sources:
                raise ValueError(f"endpoint '{ep.name}' references unknown source '{ep.source}'")
            if ep.name in seen_names:
                raise ValueError(f"duplicate endpoint name '{ep.name}'")
            route = (ep.method, re.sub(r"\{[^}]+\}", "{}", ep.path))
            if route in seen_routes:
                raise ValueError(f"duplicate route {ep.method} {ep.path}")
            seen_names.add(ep.name)
            seen_routes.add(route)
        return self

    # ------------------------------------------------------------------ io --
    @classmethod
    def load(cls, path: str | Path = DEFAULT_CONFIG) -> "ProjectConfig":
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"Config file '{p}' not found. Run 'restforge init' first.")
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        try:
            return cls.model_validate(data)
        except Exception as exc:  # pydantic ValidationError
            raise ConfigError(str(exc)) from exc

    def save(self, path: str | Path = DEFAULT_CONFIG) -> None:
        data = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        Path(path).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def endpoint(self, name: str) -> EndpointSpec | None:
        return next((e for e in self.endpoints if e.name == name), None)
