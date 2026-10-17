---
title: Getting started
screen: dashboard
---
restforge turns databases, files, internal APIs and Python functions into secured REST endpoints,
and loads files or endpoint data into database tables. Everything you define is stored in one file,
`restforge.yaml`, in your project folder. Studio and the CLI edit that same file, so you can mix them.

The usual order is:

1. **Credential** – the database login (stored encrypted).
2. **Source** – where data comes from (database, file, upstream API, Python function).
3. **Endpoint** – a URL that returns or changes data from a source.
4. **API key** – lets an application call protected endpoints.
5. **Test** – *Try it* in Studio, or `restforge call`.
6. **Serve** – start the server, or run **loads** from a scheduler.

<!-- studio -->
## In Studio

* Start Studio from the project folder (see the CLI tab), or open it and choose **Open project**.
* The **Dashboard** shows a *Getting started* checklist. Each step links to the screen that does it
  and turns green when done.
* Every screen has a **?** button that opens the matching help topic here.
* Studio saves to `restforge.yaml` on every **Save**. The previous version is kept as
  `restforge.yaml.bak`. If the file is changed outside Studio (CLI, Notepad) a yellow bar offers **Reload**.

<!-- cli -->
## With the CLI

Open PowerShell and activate restforge once per window:

```powershell
C:\apps\restforge\.venv\Scripts\Activate.ps1
```

Create a project and open Studio:

```powershell
restforge init C:\apps\hr-api --name hr-api
cd C:\apps\hr-api
restforge studio                 # desktop window
restforge studio --web           # in the browser on this PC
```

The whole flow from the command line:

```powershell
cd C:\apps\hr-api
restforge cred add hr_oracle -t database -f driver=oracle+oracledb -f host=dbhost -f port=1521 -f service_name=HRPDB -f username=hr_api
restforge source add hr -t sql --credential hr_oracle
restforge endpoint crud employees -s hr --table employees --key employee_id --read-only
restforge key create reporting-app --scope employees:read
restforge call employees-list --limit 5 --table
restforge serve
```

Help for any command: `restforge <command> --help`, for example `restforge endpoint add --help`.
This guide in the terminal: `restforge docs`, `restforge docs endpoints`, `restforge docs --search upsert`.
