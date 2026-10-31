# Changelog

All notable changes to gmi are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
From 1.0.0 the documented commands follow [Semantic Versioning](https://semver.org/).
Areas listed as not verified are excluded from that promise.

## [1.0.2] - 2026-10-31

### Changed

- `gmi mcp config --client claude-code` prints the quote-free stdio command
  first, the project `.mcp.json` block second and the POSIX-only `add-json`
  command last. Both permission flags are carried into the server arguments.
- Printed guidance explains inherited shell credentials and explicit forwarding
  without printing environment values. README and MCP troubleshooting match it.
- Package version and installer default tag are now 1.0.2 and `v1.0.2`.

### Fixed

- Windows quoting in the printed Claude Code command, reported by
  @chinmayee5599 in [pull request 10](https://github.com/zkasuran/gmi/pull/10).
  The error `Invalid configuration: : Invalid input` was reproduced on Linux
  with mangled JSON quotes. The new quote-free form was verified on Linux
  with Claude Code 2.1.316 using a stand-in server. Windows itself is still
  unverified.

### Verification

- `pytest -q -m "not live and not live_media"` passed twice: 306 passed and
  9 deselected on each run. No live API calls were made.
- `python tools/gen_api.py --check` and `sh -n install.sh` passed.
- `gmi --version` printed `gmi 1.0.2`. The Claude Code configuration command
  passed offline checks for both permission flags and matching documentation.
- Windows execution, GMI calls through Claude Code and installation from the
  new tag remain unverified. The new tag has not been created.

## [1.0.1] - 2026-10-25

### Added

- Optional `idempotency_key` argument for MCP operations accepting the
  Idempotency-Key header, with validation and the key returned in result metadata.
- `idempotency_key` column per tool in `gmi mcp tools --json` and a support count.
- Troubleshooting guide from @Ali30mhi and Cursor check from @vidhusanv17.
- Retry guidance and troubleshooting entries for PEP 668, Claude Code environment
  variables and an unconfirmed Windows quoting report.

### Changed

- Write and destructive tool errors for network failures, timeouts and HTTP 5xx
  responses warn that changes may have been made and recommend checking state.
- Write tool descriptions explain retry limits. Failed sandbox run calls warn
  about possible leftover sandboxes or templates.
- Package version and installer default tag are now 1.0.1 and `v1.0.1`.

### Not verified

- Claude Code and Cursor MCP setups have not been run here.
- Streamable HTTP transport, MCP resources and MCP prompts are not implemented.
- `gmi serve` has not been deployed on the AgentBox marketplace. The Docker image
  has only been built and run locally. Image push, GMI injecting the runtime key
  and the marketplace wizard steps have not been done; gateway access control
  remains unverified.
- Cluster write operations remain unverified.
- macOS and Windows have not been tested.

## [1.0.0] - 2026-10-25

### Added

#### Session and chat

- Interactive terminal session with streaming Markdown replies, model selection,
  system prompts, saved conversations and usage totals.
- Scriptable chat, model search, login and configuration commands with JSON output.
- Live model discovery with context sizes and prices from the API.

#### Media

- Image, video and audio generation from the session or shell.
- Live catalogue filtering for usable models, parameter validation, prices,
  request polling and artifact downloads.

#### API commands

- Commands generated from the four vendored GMI OpenAPI specs.
- Dry runs with masked credentials and confirmation for non-GET requests.
- `gmi api check` for GET probes with documented skips for missing parameters.

#### Sandbox and serve

- Sandbox templates, creation, exec, file transfer, timeout changes and deletion.
- One-shot sandbox runs that attempt cleanup and keep the reusable template.
- `gmi serve` HTTP chat service with a health endpoint and optional bearer token.
- Docker image built and run locally; sandbox commands verified live by the owner
  as described in the README.

#### MCP

- `gmi mcp serve` over stdio with the optional official Python SDK.
- Generated and curated tools with read, write and destructive permission gates,
  result redaction and output limits.
- `gmi mcp tools` and `gmi mcp config` without the SDK or credentials.
- Offline SDK client tests for both protocol eras documented in the README.
  Codex live verification is limited to the two tools recorded there.

#### Packaging

- Python 3.10 or newer with pip, pipx and uv installation options.

### Not verified

- Claude Code and Cursor MCP setups have not been run here.
- Streamable HTTP transport, MCP resources and MCP prompts are not implemented.
- `gmi serve` has not been deployed on the AgentBox marketplace. The Docker image
  has only been built and run locally. Image push, GMI injecting the runtime key
  and the marketplace wizard steps have not been done; gateway access control
  remains unverified.
- Cluster write operations remain unverified.
- macOS and Windows have not been tested.
