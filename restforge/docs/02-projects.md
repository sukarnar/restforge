---
title: Projects and files
screen: dashboard
---
A project is a folder. restforge never writes outside it.

| File / folder | What it is |
|---|---|
| `restforge.yaml` | sources, endpoints, keys (hashed), loads – the single source of truth. Safe to keep in Git. |
| `restforge.yaml.bak` | the previous version, written on every save |
| `.env` | secrets: JWT secret and the vault master key. **Never** commit or e-mail it. |
| `.restforge\credentials.vault` | encrypted credentials (needs the master key from `.env`) |
| `data\` | files that file sources read (CSV, JSON, Excel). Paths are always relative to this folder. |
| `handlers\` | Python modules for callable sources |
| `logs\audit.jsonl` | who changed what, and every API call (no data, no secrets) |
| `logs\loads\` | load history, rejects files, source versions and archived copies |

<!-- studio -->
## In Studio

* **Open project** accepts the folder or the `restforge.yaml` file; recent projects are listed.
* **Create project** makes the folder structure and a new `.env` with generated secrets.
* **Validate project** on the Dashboard builds the whole API without starting it and lists problems.
* The logout icon at the bottom of the left menu closes the project.

<!-- cli -->
## With the CLI

```powershell
restforge init C:\apps\hr-api --name hr-api     # new project
cd C:\apps\hr-api
restforge validate                              # check everything, exit code 1 on problems
restforge validate -c D:\other\restforge.yaml   # any command works on another project with -c
```

Back up a project (stop the server first if you want a consistent copy):

```powershell
Compress-Archive -Path C:\apps\hr-api -DestinationPath D:\backup\hr-api-$(Get-Date -Format yyyyMMdd).zip
```

Keep the `.env` backup somewhere safe (password manager): without the master key the vault cannot be opened.
