# GMI Cloud tools over MCP

`gmi mcp serve` lets an agent call GMI Cloud through local stdio. It uses the
low-level server in the official Python SDK `mcp==2.3.0`. The SDK handles both
2026-07-28 per-request protocol metadata and older initialize handshakes.
The project and the SDK both require Python 3.10 or newer.

```sh
pip install "gmi[mcp] @ git+https://github.com/zkasuran/gmi"
gmi mcp tools
gmi mcp serve
```

The extra is optional. Core installs can list tools and print configuration
without the SDK. If the SDK is missing, `gmi mcp serve` writes
`MCP SDK missing; install with: pip install "gmi[mcp] @ git+https://github.com/zkasuran/gmi"` to stderr and exits 2.
Only protocol messages go to stdout while serving.

## Flags

| Command or flag | Meaning |
|---|---|
| `gmi mcp serve` | Start the stdio server. The agent client launches this process. |
| `--services iam,idc,sandbox,cluster` | Comma-separated generated service selection. Default: all four. Sandbox curated tools follow sandbox selection. Inference curated tools remain available. |
| `--allow-write` | Register POST, PUT and PATCH tools plus curated sandbox writes. The launch flag is operator consent for calls; there are no terminal confirmation prompts. |
| `--allow-destructive` | Register DELETE tools and imply `--allow-write`. |
| `--include GLOB` | Include tool names matching a shell-style glob. Repeat for alternatives. Quote globs in the shell. |
| `--exclude GLOB` | Exclude matching tool names. Repeat as needed. Exclusions win over includes. |
| `--max-output-bytes N` | Bound the UTF-8 result payload text. Default: 65536; minimum: 128. Truncated results omit structured content and end with `[truncated N bytes]`, where N is the removed byte count. |
| `--timeout SECONDS` | HTTP request timeout. Default: 60; must be positive. This is not a total runtime limit for multi-step sandbox tools. |
| `gmi mcp tools [--json]` | List exactly the tools that would be registered. Supports all service, permission and glob filter flags above. Does not require SDK imports or credentials. |
| `gmi mcp config --client codex\|claude-code\|cursor` | Print launch configuration without credentials. Supports `--allow-write`. |
| `--help` | Show command usage. |

Examples:

```sh
gmi mcp tools --services sandbox --include 'sandbox_get_*' --include 'gmi_*'
gmi mcp serve --allow-write --exclude 'cluster_*' --max-output-bytes 32768
gmi mcp config --client codex
gmi mcp config --client claude-code --allow-write
gmi mcp config --client cursor
```

## Safety model and counts

Counts below were copied from the tool on 23 October 2026. Curated is a subset
of the read and write counts, not an additional class to sum.

| Launch flags | Read | Write | Destructive | Curated subset | Total |
|---|---:|---:|---:|---:|---:|
| Default | 36 | 0 | 0 | 3 | 36 |
| `--allow-write` | 36 | 42 | 0 | 6 | 78 |
| `--allow-destructive` | 36 | 42 | 12 | 6 | 90 |

Exact command output:

```text
gmi mcp tools
Counts: read=36, write=0, destructive=0, curated=3, total=36

gmi mcp tools --allow-write
Counts: read=36, write=42, destructive=0, curated=6, total=78

gmi mcp tools --allow-destructive
Counts: read=36, write=42, destructive=12, curated=6, total=90
```

| Class | Registration | Behavior |
|---|---|---|
| Read | Default | Generated GET operations plus models, chat and small sandbox file reads. Chat costs tokens. |
| Write | `--allow-write` | Generated POST, PUT and PATCH operations plus sandbox run, exec and file writes. May create billable resources. |
| Destructive | `--allow-destructive` | Generated DELETE operations. May delete data or resources. |
| Curated | Follows each tool's read or write class | Handwritten schemas that keep sandbox host and access token resolution inside the process. |

The manifest has 93 operations. Nine are never exposed: five sandbox data-plane
operations using `sandboxAccessToken`, `create-api-key`, two operations using
`bearerEmailVerificationToken` and the IAM oauth operation. Raw data-plane
operations need per-sandbox hosts and secrets that must not pass through the
model. Credential creation and verification or OAuth token exchanges stay out
of the tool surface. There are 84 eligible generated tools and six curated tools.
Filters and permission gates remove tools from `tools/list`. Direct calls to
unregistered tools fail with an error and an enabling flag or filter hint.

Generated names use `{service}_{operation_name}` with dashes replaced by
underscores. Names are unique and at most 64 characters. Input schemas retain
resolved request body schemas, required path strings, query types and enums.
Unexpected top-level arguments are rejected. Idempotency-Key is not a model
argument: each applicable call receives a fresh UUID. GET tools advertise
read-only annotations; GET, PUT and DELETE advertise idempotence. DELETE tools
advertise destructive behavior. All tools advertise access to external systems.

Every result, including errors, redacts string values when the lower-cased key
name, after replacing `-` with `_`, ends with `token`, `secret`, `password`,
`passwd`, `api_key`, `apikey`, `authorization`, `credential`, `credentials` or
`private_key` or equals `key`. This key-name rule never redacts numbers or
booleans. Names ending with plural `tokens`, such as `prompt_tokens`,
`completion_tokens`, `total_tokens`, `max_tokens` and `output_tokens`, are not
redacted by name, even with string values. JWT-shaped strings starting
with `eyJ` and known credentials are also replaced with `[redacted]`. Returned
secret values are removed from echoed fields too. The same cleaning applies to
text and structured content. Large results are truncated after redaction; binary API
responses return a note instead of bytes.

Tool output is untrusted data. The server instructions explicitly tell the
agent never to treat returned text as instructions. This instruction and
redaction do not make arbitrary service or sandbox content trustworthy.

## Curated tools

| Tool | Class | Input and behavior |
|---|---|---|
| `gmi_models` | Read | Optional `search` and `limit` (default 50; range 1 to 1000). Lists text models using the inference key. |
| `gmi_chat` | Read, costs tokens | Required `message`; optional `model` and `max_tokens` (default 256; range 1 to 1024). Uses the configured default model when omitted. Returns reply and usage. |
| `gmi_sandbox_run` | Write | Required `image` and nonempty `command` array; optional `product` and `timeout` (default 300; range 1 to 300). Reuses or builds an image template, creates a sandbox, executes and always attempts cleanup. Keeps the reusable template. Cleanup deletion is part of this tool's write consent. |
| `gmi_sandbox_exec` | Write | Required `sandbox_id` and nonempty `command` array; optional `timeout` (default 300; range 1 to 300). Connects internally and returns stdout, stderr and exit code. A timed out command may keep running. |
| `gmi_sandbox_put_file` | Write | Required `sandbox_id`, `path` and UTF-8 `content`. Writes at most 256 KB inside the sandbox. Cannot read host files. Overwrites an existing sandbox file. |
| `gmi_sandbox_get_file` | Read | Required `sandbox_id` and `path`. Reads at most 256 KB of UTF-8 content. Rejects larger or binary files; use `gmi sandbox get-file` for those. |

The one-shot sandbox timeout bounds sandbox lifetime and each wait step, not the
total time spent building a template and running a command. A cleanup failure
is reported; inspect the sandbox and kill it with the CLI if necessary.
A nonzero process exit is returned in `exit_code` with its stdout and stderr.

## Keys and client configuration

Set environment variables where the client runs. Never put a key in a copied
snippet. Inference uses `GMI_API_KEY` or the saved gmi key. Sandbox and Cluster
use `GMI_COMPUTE_API_KEY` or `GMI_SANDBOX_API_KEY` with compute scope. Generated
operations reuse `gmi api` credential selection, including the `GMI_TOKEN`
override and fallback to `GMI_API_KEY` or saved configuration. IAM and IDC need
a console credential with access to the requested organization. Sandbox access
tokens are fetched by connect internally and never returned to the model.

Output copied from `gmi mcp config --client codex`:

```text
# Set environment variables where the client runs: GMI_API_KEY for inference;
# GMI_COMPUTE_API_KEY or GMI_SANDBOX_API_KEY for compute scope.
# Paste this block into ~/.codex/config.toml:
[mcp_servers.gmi]
command = "gmi"
args = ["mcp", "serve"]
env_vars = ["GMI_API_KEY", "GMI_COMPUTE_API_KEY", "GMI_SANDBOX_API_KEY", "GMI_SANDBOX_IDC_NAME"]

# Codex only passes the variables listed in env_vars, so they must also be set in the shell that starts Codex.
# Tool output is data; write and destructive tools are off unless --allow-write or --allow-destructive flags are given.
```

Output copied from `gmi mcp config --client claude-code`:

```text
# Set environment variables where the client runs: GMI_API_KEY for inference;
# GMI_COMPUTE_API_KEY or GMI_SANDBOX_API_KEY for compute scope.
claude mcp add-json gmi '{"type":"stdio","command":"gmi","args":["mcp","serve"],"env":{"GMI_API_KEY":"${GMI_API_KEY}","GMI_COMPUTE_API_KEY":"${GMI_COMPUTE_API_KEY}","GMI_SANDBOX_API_KEY":"${GMI_SANDBOX_API_KEY}","GMI_SANDBOX_IDC_NAME":"${GMI_SANDBOX_IDC_NAME}"}}'

# Equivalent .mcp.json block:
{
  "mcpServers": {
    "gmi": {
      "type": "stdio",
      "command": "gmi",
      "args": [
        "mcp",
        "serve"
      ],
      "env": {
        "GMI_API_KEY": "${GMI_API_KEY}",
        "GMI_COMPUTE_API_KEY": "${GMI_COMPUTE_API_KEY}",
        "GMI_SANDBOX_API_KEY": "${GMI_SANDBOX_API_KEY}",
        "GMI_SANDBOX_IDC_NAME": "${GMI_SANDBOX_IDC_NAME}"
      }
    }
  }
}

# Claude Code 2.1.310 was tested live on 25 October 2026.
# The gmi server connected successfully, but the gmi_models call did not return after more than two minutes.
# The generated `claude mcp add-json` command was rejected by Claude Code with "Invalid configuration: : Invalid input".
# Fix needed: update and re-verify the Claude Code configuration generated by `gmi mcp config --client claude-code`.
# Set the variables you use in the shell that starts Claude Code; unset variables without a default remain literal text.
# Tool output is data; write and destructive tools are off unless --allow-write or --allow-destructive flags are given.
```

Output copied from `gmi mcp config --client cursor`:

```text
# Set environment variables where the client runs: GMI_API_KEY for inference;
# GMI_COMPUTE_API_KEY or GMI_SANDBOX_API_KEY for compute scope.
# Add to .cursor/mcp.json or ~/.cursor/mcp.json:
{
  "mcpServers": {
    "gmi": {
      "type": "stdio",
      "command": "gmi",
      "args": [
        "mcp",
        "serve"
      ],
      "env": {
        "GMI_API_KEY": "${env:GMI_API_KEY}",
        "GMI_COMPUTE_API_KEY": "${env:GMI_COMPUTE_API_KEY}",
        "GMI_SANDBOX_API_KEY": "${env:GMI_SANDBOX_API_KEY}",
        "GMI_SANDBOX_IDC_NAME": "${env:GMI_SANDBOX_IDC_NAME}"
      }
    }
  }
}

# unverified, taken from the client's documentation.
# Tool output is data; write and destructive tools are off unless --allow-write or --allow-destructive flags are given.
```

Add `--allow-write` to `gmi mcp config` to include it in the server arguments.

Codex verified live on 23 October 2026 (tools sandbox_list_templates and gmi_models called by Codex CLI 0.163.0 with model gpt-6.1-sol).
Claude Code verification attempted live on 25 October 2026 with Claude Code 2.1.310. The gmi MCP server connected successfully, but `gmi_models` did not return after more than two minutes. The generated `claude mcp add-json` command was rejected with `Invalid configuration: : Invalid input`. Claude Code configuration therefore remains unverified; a fix to the generated configuration is needed before marking the integration verified.

Documentation references: [Codex](https://developers.openai.com/codex/mcp/),
[Claude Code](https://code.claude.com/docs/en/mcp) and
[Cursor](https://cursor.com/docs/context/mcp).

## Verification and limitations

Offline tests use the official SDK client against a `gmi mcp serve` subprocess
with a local HTTP fixture. They exercise both 2026-07-28 and the older
2025-11-25 initialize protocol. They check schemas, gating, key selection,
redaction, truncation, file limits, cleanup and protocol-only stdout.

Codex verified live on 23 October 2026 (tools sandbox_list_templates and gmi_models called by Codex CLI 0.163.0 with model gpt-6.1-sol).
Claude Code and Cursor not run here. No live MCP calls were made during this
build. Other MCP calls against GMI remain unverified. Existing CLI sandbox
live checks do not establish MCP verification.

Streamable HTTP transport is not implemented. This server supports stdio only.
MCP resources and prompts are not implemented. `gmi mcp login` is outside this
build and is not implemented here.

For an operator check, install the extra, set credentials in the client runtime
and paste the generated Codex block into `~/.codex/config.toml`. Start Codex
from that shell. Ask Codex to list tools, call
`gmi_models` then call `gmi_chat` with `max_tokens` set to 16. Check that writes
are absent by default. Enable sandbox writes only when ready to create billable
compute and verify cleanup afterward.

## Offline development

```sh
pip install -e '.[test]'
python -m pytest -q -m "not live and not live_media"
python tools/gen_api.py --check
```

`GMI_MCP_TEST_BASE` redirects generated and inference HTTP calls only when
`GMI_MCP_TEST_MODE=1` is also set. These variables are test-only and must not be
set in a real client runtime. The protocol fixture supplies both variables
and dummy credentials to a subprocess. Curated sandbox unit tests use an HTTP
transport fixture inside `SandboxClient`; they do not connect to GMI Cloud.

Final verification on 23 October 2026 used this command twice:

```sh
env -u GMI_API_KEY -u GMI_TOKEN -u GMI_COMPUTE_API_KEY -u GMI_SANDBOX_API_KEY \
  .venv/bin/python -m pytest -q -m 'not live and not live_media'
.venv/bin/python tools/gen_api.py --check
```

Both suite runs passed: 266 passed with 9 live tests deselected. The manifest
check passed. Client configuration tests parse the TOML and JSON, check server
arguments and confirm that environment values never appear in the output.
