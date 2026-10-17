---
title: Running the server
screen: dashboard
---
`restforge serve` starts the API (uvicorn). By default it listens on `127.0.0.1:8080` – only this PC.
To serve other machines set `server.host: 0.0.0.0` in `restforge.yaml` and put TLS in front (or configure
`server.ssl_certfile` / `ssl_keyfile`). The interactive API documentation is at `/api/docs`
and works offline.

Changes to sources or endpoints need a **server restart**. Credential rotations do not.

<!-- studio -->
## In Studio

* Build and test endpoints with **Try it** – no server needed.
* **Dashboard > Validate project** runs the same checks the server runs at start-up.
* Start the server from PowerShell or as a Windows service (CLI tab).

<!-- cli -->
## With the CLI

```powershell
cd C:\apps\hr-api
restforge validate
restforge serve                         # Ctrl+C to stop
restforge serve --port 8090
restforge serve --host 0.0.0.0 --port 8080 --workers 2
```

As a Windows service with NSSM (run PowerShell as administrator):

```powershell
nssm install restforge C:\apps\restforge\.venv\Scripts\restforge.exe serve
nssm set restforge AppDirectory C:\apps\hr-api
nssm set restforge AppStdout C:\apps\hr-api\logs\service.log
nssm set restforge AppStderr C:\apps\hr-api\logs\service.log
nssm start restforge
nssm restart restforge                  # after changing endpoints
```

Check that it is up:

```powershell
Invoke-RestMethod http://localhost:8080/health
Start-Process http://localhost:8080/api/docs
```
