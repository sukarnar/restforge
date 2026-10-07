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
# .env already contains a generated RESTFORGE_JWT_SECRET; add your DB URL there:
echo HR_DB_URL=sqlite:///data/hr.db >> .env

# 1. data sources
restforge source add hr      --type sql      --url '${ENV:HR_DB_URL}' --read-write
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
* Keep `.env` out of Git; rotate API keys (`key revoke` + `key create`).
* Set `security.expose_docs: false` if the OpenAPI schema should not be public.
* With multiple workers/instances, plug in a shared (Redis) `RateLimiter`.
