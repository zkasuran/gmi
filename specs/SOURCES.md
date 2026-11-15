# Vendored GMI OpenAPI specs

Fetched 2026-10-21 from https://docs.gmicloud.ai/api-spec/. `sandbox_api.yaml`
was refreshed on 2026-11-15; the other three files were unchanged on that date.
Regenerate the CLI with `python tools/gen_api.py` after refreshing these files.

| File | Short name | Operations | SHA-256 |
|---|---|---|---|
| `ias-public-api.yaml` | iam | 30 | `7e1f5aac0e3acc63...` |
| `ids-public-api.yaml` | idc | 1 | `74adf092555f6dcb...` |
| `sandbox_api.yaml` | sandbox | 36 | `5195b2eaadd81465...` |
| `service_api.yaml` | cluster | 41 | `520a9b53386c7a06...` |

Total operations: 108.

The 2026-11-15 sandbox refresh added 15 operations: durable snapshots (create,
list, get, update, delete), sandbox pause, resume and runtime actions, template
build creation and start, template build-context files (create, get) and three
shell data-plane operations. Existing operations changed only additively:
`create-sandbox` accepts `snapshot_id` as an alternative to `template_id`,
`list-templates` gained a `name` filter and `create-sandbox-execution` gained an
optional `Idempotency-Key` header.
