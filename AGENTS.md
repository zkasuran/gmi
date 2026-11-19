# AGENTS.md: building `gmi`, the command line for GMI Cloud

Read this whole file before changing anything. It is the contract for this repo.

## What we are building

`gmi` is a community-built CLI for GMI Cloud, built in public across a six-day
takeover of @GMI_Cloud (Tue 20 Oct to Sun 25 Oct 2026). Every day's posts show
something that already runs, so **"done" means it works against the live API**,
proven by a test that calls the real endpoint. No mocks pretending to be the API.

The full plan, all committed:

1. Inference from the shell: `gmi models`, `gmi run`, `gmi chat`, plus `gmi image`,
   `gmi video`, `gmi audio` through the request queue.
2. Interactive `gmi chat` with slash commands: `/model`, `/models`, `/system`,
   `/clear`, `/save`, `/load`, `/help`, `/exit`.
3. Full API coverage generated from GMI's four published OpenAPI specs
   (108 operations: IAM 30, IDC 1, Sandbox 36, Cluster Engine 41, recounted 2026-11-15).
   These counts change when GMI updates the specs, so recount before quoting them.
4. `gmi sandbox` (GMI calls this AgentBox): create, exec, push/pull files, extend TTL, kill.
5. `gmi mcp serve` (every operation as an MCP tool) and `gmi mcp login` to `mcp.gmicloud.ai/mcp`.
6. Published as an agent on the AgentBox marketplace.

## Schedule (what must work and by when)

Posts for a day are due ~12 hours before that day starts, so each day's build must
be finished the day before.

| Takeover day | Must work by | Scope |
|---|---|---|
| Day 1, Tue 20 | **Mon 19 Oct, 20:00 IST** | `gmi auth login`, `gmi models`, `gmi chat` (one-shot + streaming), `--json` |
| Day 2, Wed 21 | Tue 20 | interactive chat with slash commands, model search, image / video / audio |
| Day 3, Thu 22 | Wed 21 | command tree generated from the four OpenAPI specs |
| Day 4, Fri 23 | Thu 22 | `gmi sandbox`, then AgentBox publish |
| Day 5, Sat 24 | Fri 23 | `gmi mcp serve`, `gmi mcp login` |
| Day 6, Sun 25 | Sat 24 | v1.0, docs, install one-liner, good first issues |

## Verified facts about the GMI APIs (checked 2026-10-21)

- The vendored specs contain 108 operations: iam 30 (6 GET, 14 POST, 6 PATCH, 4 DELETE), idc 1 (1 GET), sandbox 36 (14 GET, 16 POST, 3 PATCH, 3 DELETE; refreshed 2026-11-15) and cluster 41 (18 GET, 13 POST, 4 PUT, 6 DELETE). Operation IDs exist for 105 operations. Tags are iam {invitations, me, oauth, organizations, users}, idc {idcs}, sandbox {Sandbox, Sandbox-Exec, Sandbox-Files, Sandbox-Shell, SandboxSnapshot, SandboxTemplate} and cluster {baremetals, containers, elastic-ips, firewalls, images, logs, templates, vpcs}.
- Servers are iam and idc `https://console.gmicloud.ai/api/v1`, sandbox `https://console.gmicloud.ai/api/v2` and cluster `https://console.gmicloud.ai/api`. Eight operations have no auth, 89 use `bearerAuth`, one uses `bearerAPIKey`, two use `bearerEmailVerificationToken` and eight sandbox data-plane operations (exec, files, shell) use `sandboxAccessToken` in `X-Access-Token`. The inference key with scope `ie_model` is rejected by Sandbox and Cluster. Those services use a compute-scope key. Cluster write operations and operations that create GPU compute are untested and are not provisioned for this account.

- Inference is OpenAI-compatible. Base URL `https://api.gmi-serving.com/v1`.
  Auth: `Authorization: Bearer <API key>`. Chat: `POST /v1/chat/completions`
  (supports `stream: true`). Responses API: `POST /v1/responses`.
- `GET /v1/models` **requires** auth (401 without a key). It returns 82 models.
  Each record carries `id` (e.g. `openai/gpt-6.1-sol`, always with a provider prefix),
  `name`, `context_length`, `quantization`, `owned_by` and `pricing`
  (`prompt` and `completion` are USD per token as strings, plus optional
  `input_cache_read`, `input_cache_write` and `overrides` for long prompts).
- **Cloudflare blocks the default Python urllib User-Agent** with HTTP 403
  `error code: 1010`. Always send an explicit User-Agent such as `gmi-cli/<version>`.
- Media (image, video, audio) goes through an async request queue:
  `POST https://console.gmicloud.ai/api/v1/ie/requestqueue/apikey/requests`, then poll
  `.../requests/{id}`. The queue is shared per account: music starves TTS if both run at once.
- OpenAPI specs: `https://docs.gmicloud.ai/api-spec/{ias-public-api,ids-public-api,sandbox_api,service_api}.yaml`.
  IAM, IDC and Cluster Engine use `https://console.gmicloud.ai/api/v1` or `/api`. Sandbox uses `/api/v2` plus
  a per-sandbox `X-Access-Token` for its data plane. The Cluster Engine spec declares session-token auth only.
  Whether an API key works there is still an open question with GMI.

## AgentBox facts (owner verified live on 2026-10-22 and 2026-10-23)

- Sandbox control plane auth uses a compute-scope key in `Authorization: Bearer`.
  Reuse the Day 3 resolver for `GMI_COMPUTE_API_KEY` or `GMI_SANDBOX_API_KEY`.
  Data plane auth uses the sandbox's `X-Access-Token`, never the compute key.
  `/sandboxes/{id}/connect` returns fresh host and token values; overwrite cached values.
- This organization's IDCs are `us-central-iowa16` and `us-central-iowa2`.
  `gmi-sandbox-us` does not exist. IDC selection is flag, environment, config then
  omission for the organization default. In iowa16 the small, medium and large
  presets were active. Custom template resources are temporarily disabled.
- Template builds took 17 to 106 seconds. Poll every five seconds and stop on
  `ready` or `error`. Surface the build failure message. Template deletion is
  asynchronous and deleted templates can remain briefly in listings.
- On 22 October the platform deleted every sandbox about one second after
  creation with state `deleting` then `deleted`. The code was
  `Sandbox.BillingNoPermission`, initiator `billing` and message
  `Deleted by the platform: billing order could not be created because the organization is not permitted to purchase this product`.
  GMI cleared this organization's billing permission issue on 23 October and
  sandboxes now stay up. Accounts lacking purchase permission can still see this
  failure. Stop on the first platform deletion, report its message, code and
  initiator and exit non-zero saying this is not a gmi error. Never keep polling
  a deleted sandbox. Only initiators other than `user` are platform deletions.
  `get` reports user deletion as plain state with no error line and exit code 0.
- Owner verified live on 23 October at about 09:30 IST with the compute key from
  a clean install of main: `gmi sandbox run --image ubuntu:24.04 --yes -- sh -c "echo hello-from-sandbox; uname -sr; id -un"`
  printed `hello-from-sandbox`, `Linux 6.1.158+` and `root`, exited 0 in 18 seconds
  and left no sandbox behind. The reusable template stayed.
- `gmi sandbox create TEMPLATE --timeout 240 --wait --yes` printed
  `created sandbox ID ends TIME`. `gmi sandbox exec ID -- sh -c "echo from-exec; pwd"`
  printed `from-exec` and `/`. `gmi sandbox exec ID -- sh -c "echo oops >&2; exit 3"`
  printed `oops` on stderr and gmi exited 3.
- `gmi sandbox put ID LOCAL /tmp/in.txt` printed `uploaded LOCAL to /tmp/in.txt`.
  `gmi sandbox exec ID -- cat /tmp/in.txt` returned the content.
  `gmi sandbox get-file ID /tmp/in.txt --out OUT` printed
  `downloaded /tmp/in.txt to OUT (22 bytes)` and the download was byte for byte identical.
- `gmi sandbox get ID` showed state running, product `gmi.sandbox.small`, started
  and ends. `gmi sandbox timeout ID 1200 --yes` printed `timeout set: ID ends TIME`
  and moved the end time. Without `--yes` and without a terminal it refused.
  `gmi sandbox kill ID --yes` printed `killing sandbox ID (accepted)`. After kill,
  `get` showed state deleted, code `Sandbox.UserDeleted`, message
  `Deleted by user request` and initiator `user`. No sandbox access token or
  traffic token appeared in any output.
- The data plane is verified live on 23 October for exactly the commands above.
  Implementation tests in this update stay offline by owner instruction. No key
  is available to this build and none is requested. The owner verified the local
  Docker build and run on 22 October: non-root, healthy, 401 without the token,
  a real chat answered and no key in logs. Still unverified: image push,
  `gmi serve` on the AgentBox marketplace, GMI injecting the runtime key, gateway
  access control, Verified approval and any Cluster write operation.

## Snapshot, pause and resume facts (from the 2026-11-15 spec, offline only)

- GMI's refreshed sandbox spec adds `/snapshots` and `POST /sandboxes/{id}/pause`
  and `/resume`. A snapshot saves a running sandbox's filesystem only. A sandbox
  created with `snapshot_id` boots fresh from that disk. `create-sandbox` takes
  exactly one of `template_id` or `snapshot_id`. This answers issue #6: the
  `sandbox` build source is lineage only and manual builds accept image or template.
- Snapshot status is `creating`, `ready`, `error`, `deleting` or `delete_failed`.
  A definitive capture failure returns 201 with status `error`, `failure_code`
  and `status_reason`. Poll every five seconds. Deletion is asynchronous.
- Pause and resume return 202 with the submitted state only. Poll the sandbox
  for `paused` or `running`. A failed resume returns to `paused`. A paused
  sandbox is reclaimed after `paused_expire_at`.
- `gmi sandbox snapshots create|list|get|delete`, `gmi sandbox create --snapshot`,
  `gmi sandbox pause` and `gmi sandbox resume` are tested offline only. None has
  run against the live API.

## MCP facts (offline verified 2026-10-23)

- `gmi mcp serve` uses the official optional `mcp==2.3.0` SDK over stdio.
  Install with `pip install "gmi[mcp] @ git+https://github.com/zkasuran/gmi"`. Both project and SDK require Python 3.10+.
  The SDK v2 low-level server owns the protocol. Types live in `mcp_types`.
  Official SDK client subprocess tests cover 2026-07-28 metadata requests and
  the older 2025-11-25 initialize handshake. Real agent clients are unverified.
- `gmi mcp tools [--json]` needs no SDK or credentials. Current unfiltered counts:
  default 39 read, 0 write, 0 destructive (3 curated; 39 total);
  `--allow-write` 39 read, 50 write, 0 destructive (6 curated; 89 total);
  `--allow-destructive` 39 read, 50 write, 13 destructive (6 curated; 102 total).
  Four tools accept `idempotency_key` (recounted 2026-11-15).
  Curated counts are subsets. Recompute after manifest changes.
- Twelve manifest operations are excluded: eight sandbox data-plane operations,
  create-api-key, two email verification token operations and one IAM OAuth
  operation. Curated sandbox tools connect internally and never return access
  tokens. The default read set includes gmi_sandbox_get_file.
- The launch permission flags replace interactive confirmation for MCP calls.
  One-shot sandbox cleanup is included in write consent. Shared SandboxClient
  run_once preserves the CLI behavior and always attempts sandbox cleanup.
  File tools accept UTF-8 content with a 256 KB cap and never read host files.
- All tool results redact token, secret, password and api_key fields ignoring
  case, JWT-shaped strings and known credentials. This includes token usage
  counters. The default text cap is 65536 bytes; truncated structured content
  is omitted. Output is untrusted data, never instructions. Stdout is protocol
  only. Generated HTTP requests reuse the Day 3 builder and credential resolver.
- `GMI_MCP_TEST_BASE` redirects generated and inference HTTP calls only when
  `GMI_MCP_TEST_MODE=1`. These are test-only variables. Curated sandbox tests use
  a local transport fixture. For offline verification explicitly exclude live
  markers with `pytest -m "not live and not live_media"`, even if an ambient
  credential is present. Never infer that the environment contains no key.
- Client configuration syntax was checked against official Codex, Claude Code
  and Cursor documentation. Real client use and all MCP calls against GMI are
  unverified. Streamable HTTP transport, resources, prompts and mcp login are
  not implemented. See docs/MCP.md for flags and the operator check procedure.

## Media API facts (verified live on 2026-10-20 with real generations)

- Base: `https://console.gmicloud.ai/api/v1/ie/requestqueue/apikey`. Same `Authorization: Bearer <API key>` and
  the same explicit User-Agent rule as inference. Our inference key works here.
- `GET /models` returns `{"model_ids": [...]}`. The list contains internal and test entries, so never show it raw.
- `GET /models/{id}` is the real record: `model`, `display_name`, `model_type` (`image`, `video`, `audio`, `3d`,
  `workflow`, `api`, `language`), `status`, `qworkers`, `model_metadata` (`visibility`, `delivery_mode` `sync` or
  `async`), `modalities.input` and `.output` (lists of `{type, required}`), `price_info`
  `{pricing_type, price, unit}` and `parameters`.
- **`price_info.price` is millionths of a USD per `unit`** (per image, per second, per character, per request...).
  Example: 35000 per image is $0.035. Units vary by model, so always show the unit.
- **A model only runs if `status == "active"` AND `qworkers > 0`.** A model with `qworkers == 0` stays `queued`
  forever (observed: a request to one waited 5+ minutes and never started). Many catalogue entries have zero
  workers at any given time. There is no bulk endpoint for `qworkers`, so fetch details per model.
- `parameters[]` entries have `name`, `type` (`string`, `integer`, `float`, `boolean`, `enum`, `image`, `audio`,
  `video`, ...), `required`, `default_value` and, for enums, `options[]` of `{value, display_name}` (one voice list
  has 332 options, another 65). Some required parameters have a `default_value`; those can be auto-filled.
- `POST /requests` with `{"model": id, "payload": {...}}` returns HTTP 200 and the request record. **Sync models
  finish inside the POST**: the response already has `status: "success"` and `outcome`. Async models return
  `status: "queued"` and must be polled with `GET /requests/{request_id}`. Statuses seen: `queued`, `success`,
  `failed`. Terminal means anything outside `queued`, `dispatched`, `processing`, `pending`, `running`.
- Success `outcome` shapes (read every URL field that is present, de-duplicate):
  - image: `{"media_urls": [{"id": "0", "url": ".../x.jpg"}], "thumbnail_image_url": "..."}` (a 2048 by 2048 JPEG).
  - audio: `{"audio_url": ".../x.mp3", "media": [{"type": "audio", "url": ...}], "media_urls": [...],
    "medias": [{"id", "request_id", "url"}], "status": "audio_generated_successfully", "usage": {...}}`.
  - video: `{"video_url": "...", "thumbnail_image_url": "..."}` (per GMI docs; not yet run by us).
  Artifact URLs are public storage links and download without any auth header.
- **Upstream errors.** A provider outage returns HTTP 500 `{"error": "Upstream provider temporarily unavailable;
  please try again later", "request_id": "..."}`. GMI records that request as `status: "failed"` with
  `outcome.error {code, message}`, so nothing is running and one retry after a short wait is safe. Retry at most
  twice, only for this error. The same model failed twice in a row while another provider worked, so say which model failed
  and suggest trying another.
- `GET /requests?limit=N&offset=M` lists your past requests: `{requests, total, limit, offset, has_more}`.
- The queue is shared per account. Do not run several media jobs at once.
- Price units on usable models today include `image`, `generation`, `second`, `video`, `request`, `character`, `1k character` and `song (up to 5 minutes)`. `price_info.price` is millionths of a USD per unit.
- GMI enforces per-model daily limits. A daily limit response is HTTP 429 with a `rate_limit` object and a `Retry-After` header in seconds. The limit resets at 00:00 UTC. Daily limit responses are not retried.
- An upstream outage returns HTTP 500 with `{"error":"Upstream provider temporarily unavailable; please try again later","request_id":"..."}`. GMI records the request as failed, so a retry is safe.

## The interactive app is the product (project owner, 2026-10-18)

Running `gmi` with no arguments opens an interactive session in the terminal, the way Claude Code,
Codex CLI or kiro-cli work: you stay in it, type messages and steer it with slash commands. The
subcommands (`gmi chat "..."`, `gmi models --json`, ...) stay for scripts and CI, but the session is
what people see first and what the demos show.

## Models (project owner instruction, 18 Oct, replaces the earlier one model rule)

- **Building:** this repo is written by Codex running `openai/gpt-6.1-sol` on GMI Cloud. That is fixed in
  the builder config and is not something the code controls.
- **The product:** `gmi` supports every model GMI Cloud serves through its API: text, image, video, audio
  and anything else in the catalogue. Never hard-code a list of allowed models. Read what is available from
  the API (`GET /v1/models` for text and the media model listings) and let the user pick any of them.
- The built-in default chat model stays `openai/gpt-6.1-sol`. Media commands have sensible per-type defaults
  that the user can override and save with `gmi config set`.
- Tests: keep live calls tiny and cheap. Chat tests use `openai/gpt-6.1-sol` with `max_tokens` <= 16. Media tests
  make one small generation per type with the cheapest suitable model and are marked `live_media` so they
  can be skipped.

## Conventions

- Python 3.10+, packaged with `pyproject.toml`, console script `gmi`. Pin exact dependency versions.
  Keep dependencies few and well-known.
- Every command supports `--json` (machine-readable, no colour, nothing interactive) and returns a non-zero
  exit code on failure. Errors go to stderr with a short, actionable message.
- Key handling. Order: `--api-key` flag, then the `GMI_API_KEY` env var, then the config file at
  `~/.config/gmi/config.toml` (created mode 600). **Never print, log or echo a key.** Mask it in any
  output (`gmi_...abcd`). Never commit one. `gmi auth login` reads the key with a hidden prompt or from stdin,
  and verifies it with `GET /v1/models` before saving.
- Anything that deletes resources or spends money beyond a normal model call asks for confirmation.
  `--yes` skips the prompt.
- Tests: `pytest`. Unit tests for parsing and formatting. **Live tests** marked `@pytest.mark.live`, which run
  only when `GMI_API_KEY` is set, call the real API and keep spend tiny (`max_tokens` <= 16).
- Style: plain, specific English in help text and docs. No em or en dashes anywhere.
- Generate the API manifest with `python tools/gen_api.py`; verify it with `python tools/gen_api.py --check`.
  Resolve local references recursively for parameters, request bodies and schemas with a cycle guard.
  Merge path-level and operation parameters by name and location with the operation taking precedence.
  Offline invariants compare every operation with the vendored specs: path placeholders must match
  path parameters, parameter names and locations must be present, types default to string and required
  flags must be preserved. The manifest must contain no unresolved references.
  `gmi api check` skips operations with path parameters or missing required query parameters.
  Only the required `idc_name` query can be supplied through `--idc-name` or `GMI_SANDBOX_IDC_NAME`.
- Licence Apache-2.0. Git author `zkasuran <289388318+zkasuran@users.noreply.github.com>`.

## Release packaging facts (2026-10-25)

- The package version in `pyproject.toml` and `src/gmi/__init__.py` is 1.0.0.
  `gmi --version` prints `gmi 1.0.0`. HTTP User-Agent values use `__version__`.
- Root `install.sh` is POSIX sh. It defaults to GitHub tag `v1.0.0`;
  `GMI_REF` overrides the ref. `--mcp` or `GMI_INSTALL_MCP=1` adds the MCP extra.
  It checks Python 3.10 or newer and chooses uv, pipx then Python pip with
  `--user`. Installer tests use fake tools only. Real installation is unverified.
- `CHANGELOG.md` records release capabilities and verification limits.
  `CONTRIBUTING.md` gives human setup, test, generation and contribution guidance.
  GitHub issue forms and the pull request template live in `.github/`.
- From 1.0.0 documented commands follow semantic versioning. Areas marked as
  not verified are excluded from that promise. Preserve all existing limits.

## Roadmap after 1.0 (project owner, 2026-11-19)

gmi is becoming a full terminal agent for GMI Cloud.
[docs/ROADMAP.md](docs/ROADMAP.md) is the plan: phases 1.1 to 1.6, the target
architecture (`core/` engine and events, `tools/`, `tui/`, `headless.py`) and
the "done when" check for each phase. Work in phase order unless the owner says
otherwise. Keep the rules above: live verification, confirmation before writing,
running or spending, and no keys in project files, tool output or sessions.
Tick a roadmap box only when the item ships and is recorded in the changelog.
Phase 3 starts only after phase 2 records which GMI models support tool calling.

## How to report back

End every run with: what now works (exact commands), the test command and its result,
anything unverified and the next step. Do not claim something works unless you ran it.
