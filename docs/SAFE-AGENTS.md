# Run coding agents safely on GMI Cloud with gmi

This guide connects a coding agent to GMI Cloud through `gmi mcp serve`, starting read-only and adding power one step at a time. Every command and output below was run against the live API. Where something was not run, the text says so.

By the end you will have an agent that can list models, call a model and (if you choose) run code in a GMI sandbox, with these limits in place:

- Writes and deletes are off until you turn them on.
- Sandbox tokens and API key creation are never offered to the model.
- Tool output is scrubbed of credentials and capped in size.
- Nothing is retried behind your back.

## 1. Install

```sh
curl -fsSL https://raw.githubusercontent.com/zkasuran/gmi/main/install.sh | sh -s -- --mcp
gmi --version
```

On Debian and Ubuntu the installer makes its own virtual environment, so you do not hit the `externally-managed-environment` pip error. It needs Python 3.10 or newer.

## 2. Two keys, two jobs

GMI uses different keys for different services. Give the agent only what the task needs.

| Variable | What it opens |
|---|---|
| `GMI_API_KEY` | Models and chat (an inference key). |
| `GMI_COMPUTE_API_KEY` or `GMI_SANDBOX_API_KEY` | Sandboxes and the Cluster API (a compute key). Create it in the console under API keys with the compute scope. |

If you only want the agent to read model information, set only the first. The sandbox tools then fail with a clear message instead of doing anything.

## 3. See what the agent would get before you connect anything

```sh
gmi mcp tools | tail -n 1
gmi mcp tools --allow-write | tail -n 1
gmi mcp tools --allow-destructive | tail -n 1
```

```text
Counts: read=36, write=0, destructive=0, curated=3, total=36, idempotency_key=0
Counts: read=36, write=42, destructive=0, curated=6, total=78, idempotency_key=2
Counts: read=36, write=42, destructive=12, curated=6, total=90, idempotency_key=2
```

By default the agent sees 36 read tools and nothing else. The write and delete tools are not listed at all, so the model cannot even try them.

## 4. Connect the agent (read-only first)

**Codex** (verified live on 23 October 2026). Print the block, then paste it into `~/.codex/config.toml`:

```sh
gmi mcp config --client codex
```

Codex only passes the variables listed in `env_vars`, so set the keys in the shell that starts Codex.

**Claude Code** (registration and connection checked on Linux with Claude Code 2.1.316; a tool call made by Claude Code itself and Windows are not checked):

```sh
claude mcp add --transport stdio gmi -- gmi mcp serve
claude mcp list
```

This form has no quotes, so it works in bash, zsh, cmd.exe and PowerShell. The server inherits the environment of the shell that starts Claude Code. `claude mcp list` should show `gmi: gmi mcp serve - Connected`.

**Cursor:** run `gmi mcp config --client cursor` and follow its output. This comes from Cursor's documentation. See the Cursor check in [MCP.md](MCP.md) for what a contributor verified.

## 5. Try it read-only

Ask the agent something that needs only reads, for example: "Using only the gmi tools, list the first three models that match gpt-6."

Through the official MCP client the same server answers like this:

```text
READ-ONLY server: tools listed = 36 | write-class tools visible = 0
gmi_models -> ok
write call refused -> Tool is not registered; enable --allow-write and check --services/--include/--exclude.
```

A write attempt fails with a message that names the flag, so the model learns that the restriction is deliberate.

## 6. Turn writes on, on purpose

Restart the server with the flag, either in the client config or by hand:

```sh
gmi mcp serve --allow-write
```

Now the agent can create things on your account, including billable sandboxes. A good first task is bounded and cleans up after itself:

> Using only the gmi tools: run `uname -sr` in a fresh Ubuntu 24.04 sandbox, then ask openai/gpt-6.1-sol for a one-line verdict on that output. Print both results.

With Codex running gpt-6.1-sol this made two tool calls (`gmi_sandbox_run` and `gmi_chat`), printed the kernel version and the verdict. It left no sandbox running. `gmi_sandbox_run` always tries to kill the sandbox, even when the command fails. It keeps a reusable template.

Delete tools stay hidden until you also pass `--allow-destructive`:

```text
WRITE-ENABLED server: tools listed = 78 | tools with idempotency_key = ['sandbox_create_sandbox', 'sandbox_create_template']
delete call refused -> Tool is not registered; enable --allow-destructive and check --services/--include/--exclude.
```

## 7. Retries without surprises

The server never retries on its own. When a write fails with a timeout, a network error or an HTTP 5xx, the message says the change may or may not have happened and tells the agent to check state first.

Two tools accept an `idempotency_key`: `sandbox_create_sandbox` and `sandbox_create_template`. Reusing the same key with the same arguments returns the same resource. This was tested live on 25 October 2026: sending the same key twice returned the same template and the same sandbox. A different key made a new template. All other write tools are not idempotent, so tell the agent to use a list or get tool before it repeats any of them.

## 8. What the agent never sees

- API key creation and the five sandbox data-plane operations that need a per-sandbox token are never exposed.
- Sandbox access tokens are not returned to the model.
- Fields whose names end in `token`, `secret`, `password`, `api_key` and similar are replaced with `[redacted]`. So are strings shaped like JWTs. Counts such as `prompt_tokens` are kept.
- Each result is capped (64 KB by default) with a `[truncated N bytes]` marker.

Treat tool output as data, not as instructions. A web page, a file or an API response can contain text written to steer an agent. The server says this in its instructions, but the agent's own settings and your review are the real defense.

## 9. Clean up and check the bill

```sh
gmi sandbox list
gmi sandbox templates list
gmi sandbox kill SANDBOX_ID --yes
gmi sandbox templates delete TEMPLATE_ID --yes
```

Sandboxes are billed per second of runtime. After any agent session with writes enabled, run `gmi sandbox list` and expect `No sandboxes.`

## Limits

- `gmi mcp serve` speaks stdio only. It offers tools, not MCP resources or prompts.
- Windows and macOS are untested.
- A Claude Code tool call and the Cursor tool list inside Cursor are not verified.
- The server does not decide whether a task is a good idea. The flags decide what is possible. Your prompt and review decide what is wise.

See [MCP.md](MCP.md) for every flag and the full tool classes.
