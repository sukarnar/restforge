# restforge

Declarative, secure REST endpoints for **SQL databases, files (CSV/JSON/Excel), upstream REST APIs and Python functions** – created from a CLI, served by FastAPI, documented automatically with OpenAPI.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the design patterns and the security model.

## Install
```bash
pip install -e .            # Python 3.11+
pip install -e .[postgres]  # or [mysql], [oracle] for DB drivers
```

## Quick start
(The commands work the same in PowerShell; keep `'${ENV:...}'` in single quotes.)

```bash
restforge init hr-api --name hr-api && cd hr-api
# .env already contains a generated JWT secret and vault master key
restforge cred add hr_db -t database -f driver=sqlite -f database=data/hr.db

# 1. data sources
restforge source add hr      --type sql      --credential hr_db --read-write
restforge source add sales   --type file     --path sales.csv              # under ./data
restforge source add pricing --type callable --module handlers.pricing     # ./handlers/pricing.py
restforge source add todos   --type rest     --base-url https://jsonplaceholder.typicode.com --allow-host

# 2. endpoints
restforge endpoint crud employees -s hr --table employees --key id \
    --field name:string:required --field dept --field salary:number --filter dept
restforge endpoint add dept-summary -s hr -p /reports/departments --param dept --scope reports:read \
    --query "select dept, count(*) n from employees where (:dept is null or dept = :dept) group by dept"
restforge endpoint add sales-list -s sales -p /sales --filter region --columns order_id,region,amount --scope sales:read
restforge endpoint add quote -s pricing -p /quote/{symbol} --target quote --param symbol:string:path --param qty:integer --scope pricing:read
restforge endpoint add todo  -s todos -p /todos/{id} --param id:integer:path --upstream-path /todos/{id} --scope todos:read

# 3. credentials
restforge key create reporting --scope employees:read --scope reports:read --expires-days 90
restforge user add alice --scope employees:read --scope employees:write   # prompts for password

# 4. check & run
restforge validate
restforge endpoint list
restforge serve --port 8080          # docs at http://127.0.0.1:8080/api/docs
```

### Calling the API
```bash
curl -H "X-API-Key: rf_xxxxxxxx_..." "http://127.0.0.1:8080/api/employees?dept=ENG&limit=20"

TOKEN=$(curl -s -X POST localhost:8080/api/auth/token -H 'content-type: application/json' \
        -d '{"username":"alice","password":"..."}' | jq -r .access_token)
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
     -d '{"name":"Dev","dept":"ENG","salary":120000}' localhost:8080/api/employees
```

## Credential management
Connection secrets (DB logins, API tokens, OAuth clients…) are stored as **typed, encrypted credentials** and
referenced by name – they never appear in `restforge.yaml`.

```bash
restforge cred types                                   # database | basic | bearer | api_key | oauth2 | generic

# Oracle login – password is prompted (hidden) and special characters are escaped for you
restforge cred add hr_oracle -t database -f driver=oracle+oracledb -f host=db.corp.local -f port=1521 \
    -f service_name=ORCLPDB1 -f username=hr_api -f schema=hr --expires-days 90
restforge cred test hr_oracle                          # real login check
restforge source add hr -t sql --credential hr_oracle

# Upstream APIs
restforge cred add crm_token -t bearer                 # prompts for token
restforge cred add weather -t api_key -f query_param=appid
restforge cred add erp -t oauth2 -f token_url=https://login.corp.com/oauth2/token -f client_id=restforge -f scope=api.read
restforge source add crm -t rest --base-url https://api.crm.com --allow-host --credential crm_token

# Anything else: reference fields anywhere in the config
restforge cred add partner -t generic --secret signing_key -f tenant=acme
restforge source add partner -t rest --base-url https://api.partner.com --allow-host \
    --header 'X-Tenant=${CRED:partner.tenant}' --header 'X-Signature=${CRED:partner.signing_key}'

# Python handlers get ONLY the credentials you allow
restforge source add billing -t callable --module handlers.billing --allow-credential stripe
#   def charge(amount: int, ctx=None): key = ctx.credentials.field("stripe", "key")

restforge cred list          # names, types, expiry, which sources use them – never secrets
restforge cred show NAME     # masked; --reveal asks for confirmation
restforge cred rotate NAME   # new secret; running servers pick it up and rebuild pools, no restart
restforge cred rekey         # re-encrypt the whole vault under a new master key
restforge cred remove NAME   # refuses while a source still uses it
```

**Where credentials live** (`security.credentials.providers`, checked in order – first match wins):

| Provider | Use for | Setup |
|---|---|---|
| `vault` (default) | Local/VPS installs. `.restforge/credentials.vault`, AES-256-GCM per credential | master key in `.env` (`RESTFORGE_MASTER_KEY`, created by `init`) |
| `env` (default) | Docker/Kubernetes/CI. `RF_CRED_<NAME>_TYPE`, `RF_CRED_<NAME>_<FIELD>` | set env vars |
| `keyring` | Windows Credential Manager / macOS Keychain | `pip install -e .[keyring]` |
| `hashicorp` | Enterprise secret store (KV v2) | `VAULT_ADDR`, `VAULT_TOKEN` |

Non-interactive (CI): `restforge cred add db -t database ... --secret-from-env password=DB_PASSWORD`.

## CLI reference
| Command | Purpose |
|---|---|
| `init [DIR] --name` | New project: `restforge.yaml`, `.env` (generated JWT secret), `.gitignore`, `data/`, `handlers/`, `logs/` |
| `source add/list/remove` | Register SQL / file / REST / callable sources |
| `endpoint add` | One endpoint. `--param name:type[:query\|path\|body][:required]`, `--scope`, `--public`, `--rate-limit` |
| `endpoint crud` | list/get/create/update/delete for a table with read/write scopes (`--read-only` for list+get) |
| `endpoint list/show/remove` | Inspect and manage endpoints |
| `key create/list/revoke` | API keys (plaintext shown once, hash stored, default 90-day expiry) |
| `user add/remove` | Users for JWT login (`POST /api/auth/token`) |
| `cred init/types/add/list/show/rotate/test/rekey/remove` | Connection credentials (see above) |
| `source set-credential SOURCE [CRED]` | Attach/detach a credential on an existing source |
| `validate` | Full build of the app without serving – use in CI |
| `serve [--reload] [--workers]` | Run with uvicorn |

All commands accept `-c/--config` to point at another project file.

## Custom functions
```python
# handlers/pricing.py
from restforge import SourceError

def quote(symbol: str, qty: int = 1, ctx=None):   # ctx is optional: principal, request_id, …
    price = {"AAPL": 190.0}.get(symbol.upper())
    if price is None:
        raise SourceError("Unknown symbol", 404)
    return {"symbol": symbol.upper(), "total": price * qty}
```

## Embedding
```python
from restforge import create_app
app = create_app("restforge.yaml")   # a normal FastAPI app – mount it, add routes, test with TestClient
```

## Tests
```bash
pip install -e .[dev] && pytest
```

## Production checklist
* Run behind TLS (Traefik/Nginx) or set `server.ssl_certfile/ssl_keyfile`.
* Use least-privilege DB accounts; keep SQL sources `read_only` unless writes are required.
* Back up `RESTFORGE_MASTER_KEY` (password manager) – without it the vault cannot be decrypted.
* Keep `.env` out of Git; rotate API keys (`key revoke` + `key create`).
* Set `security.expose_docs: false` if the OpenAPI schema should not be public.
* With multiple workers/instances, plug in a shared (Redis) `RateLimiter`.
