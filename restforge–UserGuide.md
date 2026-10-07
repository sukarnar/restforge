# restforge – User Guide

Oct 7, 2026 · @Sukarna

## Overview

restforge turns databases, files, upstream APIs and Python functions into secured REST endpoints, configured entirely from the command line. Setup takes seven steps: create a project, store credentials, add sources, add endpoints, issue access, start the server, verify.

| Concept | What it is | Created with |
| --- | --- | --- |
| Project | A folder holding `restforge.yaml` (all endpoint definitions), `.env` (secrets), `data\`, `handlers\`, `logs\` | `restforge init` |
| Credential | An encrypted login or token that restforge uses to reach a database or API | `restforge cred add` |
| Source | A connection to one backend: `sql`, `file`, `rest` or `callable` | `restforge source add` |
| Endpoint | One URL + method mapped to an operation on a source | `restforge endpoint add` / `crud` |
| Scope | A permission label an endpoint requires, e.g. `employees:read`; `*` = admin | `--scope` on endpoints and keys |
| API key | What your callers send in the `X-API-Key` header | `restforge key create` |
| User | A person who logs in for a short-lived JWT token | `restforge user add` |

Two kinds of secret, never mixed up: **credentials** are what restforge uses to log in to your systems (outbound); **API keys and users** are what callers use to log in to restforge (inbound).

All commands below are PowerShell on Windows Server and run from the project folder with the virtual environment active.

## Prerequisites and installation

You need Python 3.12 or later and the restforge package; installation is a one-time step per server.

1. Install Python 3.12+ from python.org. Tick **Install for all users** and **Add Python to PATH** (needed if restforge later runs as a service under another account).
2. Unzip `restforge.zip`, for example to `C:\apps\restforge` (the folder containing `pyproject.toml`).
3. Create and activate a virtual environment, then install:

```powershell
cd C:\apps\restforge
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

4. If PowerShell blocks `Activate.ps1`, run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
5. Install the driver for each database you will use:

| Database | Install command | `driver=` value |
| --- | --- | --- |
| Oracle | `pip install oracledb` | `oracle+oracledb` |
| PostgreSQL | `pip install "psycopg[binary]"` | `postgresql+psycopg` |
| MySQL / MariaDB | `pip install pymysql` | `mysql+pymysql` |
| SQL Server | `pip install pyodbc` + Microsoft ODBC Driver 18 | `mssql+pyodbc` |
| SQLite | built in | `sqlite` |

6. Optional: `pip install -e .[keyring]` to store credentials in Windows Credential Manager.
7. Check: `restforge --help` lists the commands. If `restforge` is not recognised, the environment is not active; use `python -m restforge` instead.

In every new PowerShell window, re-activate the environment with `C:\apps\restforge\.venv\Scripts\Activate.ps1` before running restforge commands.

## Step 1 – Create a project

`restforge init` creates a project folder with a generated JWT secret and credential-vault master key.

```powershell
cd C:\apps
restforge init hr-api --name hr-api
cd hr-api
```

| Created | Purpose |
| --- | --- |
| `restforge.yaml` | Sources, endpoints, API-key hashes, users, security settings |
| `.env` | `RESTFORGE_JWT_SECRET` and `RESTFORGE_MASTER_KEY`; never commit or email it |
| `.gitignore` | Keeps `.env`, `logs\` and `.restforge\` out of Git |
| `data\` | Files served by `file` sources (they cannot read outside it) |
| `handlers\` | Your Python modules for `callable` sources |
| `logs\` | `audit.jsonl`: one line per API call |

Back up `RESTFORGE_MASTER_KEY` from `.env` to a password manager now. Without it the credential vault cannot be decrypted.

A project created before credential management existed has no master key: run `restforge cred init` once.

## Step 2 – Store connection credentials

Each database login or API token is stored once, encrypted, under a name; sources refer to that name. Secret fields (password, token, key, client secret) are always prompted with hidden input, so they never reach your PowerShell history.

**See the available types and their fields:** `restforge cred types`

| Type | Required fields | Optional fields | Use for |
| --- | --- | --- | --- |
| `database` | `driver` (+ `host` or `dsn`) | `port`, `username`, `password`, `database`, `service_name`, `sid`, `schema`, `options` | Any SQL database |
| `basic` | `username`, `password` | – | HTTP Basic auth |
| `bearer` | `token` | – | `Authorization: Bearer` token |
| `api_key` | `key` | `header` (default `X-API-Key`), `query_param` | API key in a header or query string |
| `oauth2` | `token_url`, `client_id`, `client_secret` | `scope`, `audience` | OAuth2 client credentials (token fetched and refreshed automatically) |
| `generic` | any | any | Values used via `${CRED:name.field}` or by Python handlers |

**Add database credentials:**

```powershell
# Oracle (service name)
restforge cred add hr_oracle -t database -f driver=oracle+oracledb -f host=db.corp.local `
  -f port=1521 -f service_name=ORCLPDB1 -f username=hr_api -f schema=hr --expires-days 90

# PostgreSQL
restforge cred add sales_pg -t database -f driver=postgresql+psycopg -f host=pg01 -f port=5432 `
  -f database=sales -f username=api_ro

# SQL Server
restforge cred add erp_mssql -t database -f driver=mssql+pyodbc -f host=sql01 -f port=1433 `
  -f database=ERP -f username=api_ro -f "options=driver=ODBC Driver 18 for SQL Server;TrustServerCertificate=yes"

# SQLite (no password)
restforge cred add local_db -t database -f driver=sqlite -f database=data/hr.db
```

For older Oracle databases use `-f sid=ORCL` instead of `service_name`; for a TNS alias use `-f dsn=HRPROD`. Special characters in passwords need no escaping.

**Add API credentials:**

```powershell
restforge cred add crm_token -t bearer
restforge cred add weather -t api_key -f query_param=appid
restforge cred add erp_oauth -t oauth2 -f token_url=https://login.corp.com/oauth2/token `
  -f client_id=restforge -f scope=api.read
restforge cred add partner -t generic --secret signing_key -f tenant=acme
```

**Check what is stored and test it:**

```powershell
restforge cred list                 # names, types, expiry, which sources use them
restforge cred show hr_oracle       # fields with secrets masked
restforge cred test hr_oracle       # real login (DB) or token fetch (OAuth2)
restforge cred test crm_token --url https://api.crm.com/v1/me
```

| `cred add` option | Purpose |
| --- | --- |
| `-t, --type` | Credential type (table above) |
| `-f, --field KEY=VALUE` | Non-secret field; repeat as needed |
| `--secret NAME` | `generic` only: marks a field as secret (prompted) |
| `--secret-from-env FIELD=VAR` | Read a secret from an environment variable, for unattended scripts |
| `--expires-days N` | Warn near expiry; refuse use after it |
| `--provider` | Store in `keyring` (Windows Credential Manager) or `hashicorp` instead of the vault |
| `--description` | Free-text note |

Credentials live in `.restforge\credentials.vault`, each encrypted with AES-256-GCM. On a server, keep the default vault: Windows Credential Manager entries are per user and a service running under another account will not see them.

## Step 3 – Add data sources

A source is one named connection; every endpoint points at a source. SQL sources are read-only unless you add `--read-write`.

**SQL database** (credential from Step 2):

```powershell
restforge source add hr -t sql --credential hr_oracle               # SELECT only
restforge source add hr_rw -t sql --credential hr_oracle --read-write  # allows insert/update/delete
```

Ask your DBA for a least-privilege account that can only touch the tables you expose, for example in Oracle:

```sql
CREATE USER hr_api IDENTIFIED BY "Str0ng#Pass";
GRANT CREATE SESSION TO hr_api;
GRANT SELECT ON hr.employees TO hr_api;   -- add INSERT/UPDATE/DELETE only for write endpoints
```

**File** (CSV, JSON, JSON Lines, Excel; the file must sit inside `data\`):

```powershell
Copy-Item C:\exports\sales.csv .\data\
restforge source add sales -t file --path sales.csv
restforge source add budget -t file --path budget.xlsx --sheet FY27
```

**Upstream REST API** (the host must be allow-listed; `--allow-host` adds it):

```powershell
restforge source add crm -t rest --base-url https://api.crm.com --allow-host --credential crm_token
restforge source add partner -t rest --base-url https://api.partner.com --allow-host `
  --header 'X-Tenant=${CRED:partner.tenant}'
```

**Python function** (module inside `handlers\`):

```powershell
restforge source add pricing -t callable --module handlers.pricing
restforge source add billing -t callable --module handlers.billing --allow-credential stripe
```

A handler is a plain function; it receives only the parameters declared on the endpoint, plus `ctx` if it asks for it:

```python
# handlers\pricing.py
from restforge import SourceError

def quote(symbol: str, qty: int = 1, ctx=None):
    price = {"AAPL": 190.0}.get(symbol.upper())
    if price is None:
        raise SourceError("Unknown symbol", 404)
    # ctx.credentials.field("stripe", "key") works only for credentials listed with --allow-credential
    return {"symbol": symbol.upper(), "total": price * qty}
```

Create an empty `handlers\__init__.py` so Python treats the folder as a package.

**Manage sources:**

```powershell
restforge source list
restforge source set-credential hr hr_oracle_v2   # switch credential
restforge source remove sales                     # refused while endpoints still use it
```

## Step 4 – Create endpoints

An endpoint maps one URL and HTTP method to an operation on a source. Every endpoint requires a login unless you add `--public`, and should name the scope a caller needs.

**Parameters** use the shorthand `--param name:type[:location][:required]`:

| Part | Values | Default |
| --- | --- | --- |
| `type` | `string`, `integer`, `number`, `boolean` | `string` |
| `location` | `query` (`?x=1`), `path` (`/items/{id}`), `body` (JSON) | `query` |
| `required` | add `:required` | optional (path params are always required) |

Unknown parameters are rejected, strings are capped at 1,024 characters, and list endpoints accept `limit` (default 100, maximum 500) and `offset`.

**A. CRUD for a SQL table** – generates list, get, create, update, delete with `<resource>:read` and `<resource>:write` scopes:

```powershell
restforge endpoint crud employees -s hr_rw --table employees --key employee_id `
  --field first_name:string:required --field last_name --field department_id:integer `
  --filter department_id
# read-only source or read-only API: add --read-only (list + get only)
```

| Generated | Method and path | Scope |
| --- | --- | --- |
| `employees-list` | `GET /api/employees?department_id=&limit=&offset=` | `employees:read` |
| `employees-get` | `GET /api/employees/{employee_id}` | `employees:read` |
| `employees-create` | `POST /api/employees` | `employees:write` |
| `employees-update` | `PATCH /api/employees/{employee_id}` | `employees:write` |
| `employees-delete` | `DELETE /api/employees/{employee_id}` | `employees:write` |

Only the key, `--field` and `--filter` columns are returned or writable, so leave sensitive columns out. Use lowercase table and column names; add a schema as `--table hr.employees` if the credential has no `schema` field.

**B. Custom SQL query** – write `:name` placeholders; values are always bound, never pasted into SQL:

```powershell
restforge endpoint add dept-headcount -s hr -p /reports/headcount --param dept_id:integer `
  --query "select department_id, count(*) cnt from employees where (:dept_id is null or department_id = :dept_id) group by department_id" `
  --scope reports:read
```

**C. File source:**

```powershell
restforge endpoint add sales-list -s sales -p /sales --filter region `
  --columns order_id,region,amount --scope sales:read
restforge endpoint add sales-get -s sales -p /sales/{order_id} --key order_id --scope sales:read
```

**D. Upstream REST API:**

```powershell
restforge endpoint add crm-account -s crm -p /crm/accounts/{id} --param id:integer:path `
  --upstream-path /v2/accounts/{id} --scope crm:read
```

**E. Python function:**

```powershell
restforge endpoint add quote -s pricing -p /quote/{symbol} --target quote `
  --param symbol:string:path --param qty:integer --scope pricing:read
```

**Other useful options:** `--rate-limit 30` (requests per minute for this endpoint), `--description "..."`, `--max-limit 1000`, `--public` (no login; the CLI warns).

**Manage endpoints:**

```powershell
restforge endpoint list            # method, path, name, source, scope
restforge endpoint show quote      # full definition
restforge endpoint remove quote
```

## Step 5 – Give access: API keys, users and scopes

Callers authenticate with an API key (applications) or a JWT from a user login (people). A caller can use an endpoint only if its scopes include the endpoint's scope; `*` grants everything.

**API keys for applications** – one key per calling application:

```powershell
restforge key create reporting-app --scope employees:read --scope reports:read
restforge key create hr-sync --scope employees:read --scope employees:write --expires-days 30
restforge key create admin --scope '*'          # for you: also unlocks /api/_meta/*
```

The key (`rf_<id>_<secret>`) is printed **once**; only its hash is stored. Copy it straight into the calling application's secret storage. Keys expire after 90 days by default (`--expires-days 0` = never).

```powershell
restforge key list                # id, name, active/revoked, expiry, scopes
restforge key revoke 1be09ddf     # by id
```

**Users for people** – they exchange a username and password for a 30-minute token:

```powershell
restforge user add alice --scope employees:read --scope employees:write   # password prompted, 12+ characters
restforge user remove alice
```

```powershell
$t = Invoke-RestMethod -Method Post http://localhost:8080/api/auth/token `
      -ContentType 'application/json' -Body '{"username":"alice","password":"..."}'
Invoke-RestMethod http://localhost:8080/api/employees -Headers @{ Authorization = "Bearer $($t.access_token)" }
```

Login is limited to 5 attempts per minute per IP address and per username.

**Restart the server after any key or user change**; it reads them at startup.

## Step 6 – Validate and start the server

Always run `restforge validate` first: it builds the whole application without serving and fails with a clear list of problems.

```powershell
restforge validate
# ✓ 7 endpoints across 3 sources are valid
restforge serve
# Uvicorn running on http://127.0.0.1:8080
```

`validate` checks the configuration, endpoint definitions, table and column names, and that every credential exists, decrypts and has not expired. It does not open a database connection; `restforge cred test` does that.

| `serve` option | Effect |
| --- | --- |
| `--host 0.0.0.0` | Accept connections from other machines (default `127.0.0.1` = this server only) |
| `--port 9000` | Listen on another port (default 8080) |
| `--workers 4` | Run 4 processes for more load |
| `--reload` | Restart on changes to `restforge.yaml` or handlers (development only) |
| `-c C:\apis\hr\restforge.yaml` | Run a project from another folder |

Defaults for host, port and TLS certificate files can also be set in the `server:` section of `restforge.yaml`. Stop the server with **Ctrl+C**.

With more than one worker, each keeps its own rate-limit counters, so effective limits multiply by the worker count.

## Step 7 – Verify everything works

Check from the inside out: credential, network, server, then a real endpoint. Use `curl.exe` (plain `curl` is a PowerShell alias for something else).

1. **Credential logs in:** `restforge cred test hr_oracle` → `✓ Connected to database with 'hr_oracle'`.
2. **Network path, if step 1 fails:** `Test-NetConnection db.corp.local -Port 1521` → `TcpTestSucceeded : True`.
3. **Server is up:** open `http://127.0.0.1:8080/health` → `{"status":"ok"}`.
4. **Each source is reachable** (admin key):

   ```powershell
   curl.exe -H "X-API-Key: rf_admin..." http://127.0.0.1:8080/api/_meta/health
   # {"hr": true, "sales": true}   false = that source is unreachable
   ```
5. **A real call returns data:**

   ```powershell
   curl.exe -H "X-API-Key: rf_..." "http://127.0.0.1:8080/api/employees?limit=5"
   curl.exe -H "X-API-Key: rf_..." http://127.0.0.1:8080/api/employees/101
   Invoke-RestMethod http://127.0.0.1:8080/api/employees -Headers @{ "X-API-Key" = "rf_..." }
   ```
6. **Security behaves:** no key → `401`; a key without the scope → `403`; an unknown parameter → `422`.
7. **Interactive docs:** open `http://127.0.0.1:8080/api/docs`, click **Authorize**, paste a key and try each endpoint.

**Admin views** (need a `*` key):

| URL | Shows |
| --- | --- |
| `/api/_meta/endpoints` | Every endpoint, its source and scopes |
| `/api/_meta/health` | `true`/`false` per source |
| `/api/_meta/credentials` | Credential names, types, expiry and which sources use them (no secrets) |

Every call is recorded in `logs\audit.jsonl` (time, endpoint, caller, status, duration, IP).
