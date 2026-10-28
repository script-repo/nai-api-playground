# NAI 2.8 API playground

Local console for the Nutanix Enterprise AI 2.8 management API, direct inference API, and Agent Gateway.

## Run

```powershell
python server.py --open
```

Then open http://127.0.0.1:8787 if the browser does not open on its own.

Set the cluster origin (`https://your-nai-host`), a management username and password, and an inference or Agent Gateway API key. The server listens on loopback and proxies requests, so the browser can reach a cluster that does not send CORS headers. Self-signed certificates are allowed when that checkbox is on.

Secrets are saved in this browser only if you check **Remember secrets**. The copied curl command includes the live `Authorization` header.

## Rebuild the catalog

`static/catalog.json` is generated from the OpenAPI document in `openapi/nai-2.8.0.json`, extracted from the [NAI 2.8 API reference](https://www.nutanix.dev/api_reference/apis/nai2.8.html) (page last updated 2026-08-20).

```powershell
python scripts/build_catalog.py
```
