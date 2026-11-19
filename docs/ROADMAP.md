# gmi roadmap: from CLI to a full terminal agent

Written 19 November 2026 against gmi 1.0.2 and `main` at `37ed485`.
This is a plan, not a record. Nothing below counts as shipped until it runs
against the live GMI API and a test proves it, as [AGENTS.md](../AGENTS.md)
requires. Each item moves to the [changelog](../CHANGELOG.md) when it ships.

## Goal

`gmi` becomes a full terminal agent for GMI Cloud. You run `gmi` in a project,
describe a task and the agent reads files, edits code and runs commands. It asks before anything
risky and shows every change. It works with any model GMI serves.

What makes gmi different:

- **Any GMI model, one key.** Switch between providers mid-session and see the
  real price of every turn in dollars, read from `GET /v1/models` pricing.
- **Commands run in a sandbox if you choose.** With `--sandbox`, shell tools run
  in a GMI AgentBox instead of on your machine. Your laptop stays untouched and
  snapshots make a known-good environment reusable.
- **Your cloud in the same session.** The generated API, sandbox and media
  commands are available to the agent as tools, behind the same permission gates.

## Where gmi is today

| Area | Today in 1.0.2 |
|---|---|
| Interactive session | Inline prompt with history, multiline input, streaming Markdown, Ctrl+C cancel |
| Slash commands | `/help`, `/model`, `/models`, `/media`, `/image`, `/video`, `/audio`, `/system`, `/clear`, `/save`, `/load`, `/usage`, `/login`, `/exit` |
| Models | Live catalogue, fuzzy picker, any model ID |
| Scripting | `gmi chat "..."`, `--json`, stdin piping |
| Cloud | 108 generated API commands, `gmi sandbox` with snapshots, pause and resume |
| Agents | `gmi mcp serve` exposes gmi to other agents with read, write and destructive gates |
| Distribution | `install.sh`, pip from Git, Docker image on GHCR |

gmi does not have the core of a terminal agent yet:

- no tool calling, so the model cannot read files, edit code or run commands
- no permission system for actions, project memory file or `@file` context
- no MCP client to use other servers' tools
- no headless agent mode, session resume or context compaction
- no cost in dollars, CI test workflow or PyPI, Homebrew or Windows installer

## Capability checklist

What a full terminal agent needs and where gmi stands. Status is **Have**,
**Partial** or the phase that delivers it.

| Capability | gmi |
|---|---|
| Inline interactive session with streaming | **Have** |
| Model switching | **Have**, any GMI model |
| Saved sessions | **Partial**: manual `/save` and `/load`. Auto-save and `--resume` in phase 1 |
| Headless mode with JSON events | Phase 1 |
| Cost and context display | **Partial**: token counts. Dollars and context in phase 1 |
| Self-update and doctor | Phase 1 |
| `@file` mentions and `!shell` | Phase 2 |
| Project memory file | Phase 2: reads `AGENTS.md` and `GMI.md` |
| Context compaction | Phase 2 |
| Read, search, edit and write file tools | Phase 3 |
| Shell tool | Phase 3 |
| Permission modes and allow rules | Phase 3 |
| Diff preview and undo | Phase 3 |
| Plan mode | Phase 3 |
| Remote sandbox execution on GMI AgentBox | Phase 4 |
| MCP client | Phase 5 |
| Custom slash commands and hooks | Phase 5 |
| Subagents | Phase 5 |
| Status line, themes, vim keys | Phase 6 |
| Package managers and standalone binaries | Phase 6 |
| gmi as an MCP server for other agents | **Have** |

## Architecture changes

Today `app/session.py` builds requests, streams and renders in one class.
An agent needs the loop separated from the screen so the interactive session,
headless mode and tests share one engine.

```text
src/gmi/
  core/
    engine.py       agent loop: model call, tool calls, results, repeat
    events.py       typed events: text delta, tool request, tool result, cost, done
    messages.py     conversation model including tool calls and results
    context.py      memory files, @file content, token budget, compaction
    permissions.py  modes, allow and deny rules, approval requests
    pricing.py      dollars per turn from model pricing
  tools/
    files.py        read, list, glob, grep, edit, write
    shell.py        local shell with timeout and output cap
    sandbox.py      the same shell and file tools inside an AgentBox
    cloud.py        generated API and media as gated tools
    mcp_client.py   tools from external MCP servers
  tui/              today's app/ package: input, rendering, pickers, slash commands
  headless.py       gmi -p: text, json or stream-json output
```

The engine yields events. The terminal UI renders them and answers approval
requests with prompts. Headless mode prints them as JSON lines and answers
approval requests from flags and rules. Tests drive the engine with a local
fake model server, the same way the sandbox and MCP tests use fake servers today.

Settings move to layers, with later layers overriding earlier ones:
built-in defaults, `~/.config/gmi/config.toml`, `.gmi/settings.toml` in the
project, environment variables, then flags. API keys stay only in the user file
or the environment, never in project settings.

## Phases

Each phase is a minor release. All of them keep the 1.x promise: existing
documented commands and `--json` output keep working.

### Phase 1: foundations (1.1)

Make gmi installable and measurable like a mainstream tool before adding the agent.

- [ ] CI on GitHub Actions: offline suite on Linux, macOS and Windows with Python 3.10 and 3.13
- [ ] Publish to PyPI so `pipx install gmi` and `uv tool install gmi` work. The name `gmi` returned 404 on PyPI on 19 November 2026, which suggests it is free. Confirm before relying on it
- [ ] Split `core/engine.py` and `core/events.py` out of `app/session.py` with no behaviour change
- [ ] Layered settings with `gmi config list --show-origin`
- [ ] Sessions save automatically. `gmi --continue` resumes the latest session in this directory and `gmi --resume` opens a picker
- [ ] `gmi -p "prompt"` headless mode with `--output-format text|json|stream-json`
- [ ] Dollar cost per turn and per session from model pricing. `/cost` and `/context` show spend and context use
- [ ] `gmi doctor` checks Python, keys and their scopes, network, terminal and config. `gmi update` upgrades through the installer that was used
- [ ] Make the media retry wait patchable so the offline suite drops about 15 seconds

**Done when:** a fresh install from PyPI resumes a session. `gmi -p` returns
stream-json against the live API and CI passes on all three systems.

### Phase 2: project context (1.2)

Give the model the right context without the user pasting it.

- [ ] Read `AGENTS.md` and `GMI.md` from the project root and parents at session start. `/memory` shows and edits them and `/init` drafts one
- [ ] `@path` mentions with completion, including directories and line ranges. Respect `.gitignore` and size limits
- [ ] `!command` runs a shell command the user typed and adds its output to the conversation
- [ ] Track tokens against each model's `context_length`. Warn at 80 percent. `/compact` summarises older turns and auto-compact is optional
- [ ] Images in prompts for vision models, from a path or the clipboard
- [ ] Probe which GMI models support tool calling, streamed tool calls and images. Record the results in AGENTS.md as verified facts and show them in the model picker

**Done when:** a live session answers questions about a real repository using
memory and `@file` context. The capability table is recorded from live calls.

### Phase 3: the agent (1.3)

The main step: the model can act, with the user in control.

- [ ] Engine loop with OpenAI-style `tools` and `tool_calls`, parallel calls and a step limit
- [ ] File tools: read, list, glob, grep, edit by exact replacement and write. Paths are confined to the workspace unless allowed
- [ ] Shell tool with working directory, timeout, output cap and Ctrl+C or Esc to stop
- [ ] Permission modes: `ask` (default), `accept-edits`, `plan` (read only, proposes a plan) and `auto` for trusted directories. Shift+Tab cycles modes
- [ ] Allow and deny rules in settings, for example `shell(git status)`, `shell(npm test*)` and `edit(src/**)`. Rules for `rm`, force pushes and secrets files deny by default
- [ ] Coloured diff before each edit with yes, no, always for this file and edit-before-apply
- [ ] Checkpoints of every file the agent touches. `/rewind` restores files and conversation to an earlier turn
- [ ] Tool output is treated as data. Redact known keys from tool output with the existing redaction rules
- [ ] A clear message when the chosen model cannot call tools, with a suggestion of one that can

**Done when:** with `openai/gpt-6.1-sol` the agent fixes a failing test in a
sample repository live. Every edit and command needs approval in `ask` mode.
`/rewind` restores the files and offline tests cover each permission path.

### Phase 4: sandbox agent (1.4)

Use GMI's own infrastructure as the place where the agent works.

- [ ] `gmi --sandbox` runs shell and file tools in an AgentBox and syncs the workspace in and changes back as a reviewable diff
- [ ] `/sandbox` starts, pauses, resumes and snapshots the session's sandbox
- [ ] Choose a starting snapshot or template per project in `.gmi/settings.toml`
- [ ] Show sandbox runtime cost next to token cost
- [ ] Cloud tools: the generated API, sandbox and media commands as agent tools with the existing read, write and destructive gates

**Done when:** live, the agent installs a package and runs tests inside a
sandbox and returns a diff. The sandbox is cleaned up or paused as configured.

### Phase 5: extensibility (1.5)

Let users and teams shape gmi without changing its code.

- [ ] MCP client: servers from `.gmi/mcp.json` and the user config, `/mcp` to list and enable them and permission rules per tool
- [ ] Custom slash commands from Markdown files in `.gmi/commands/` and the user config, with arguments
- [ ] Hooks on session start, prompt submit, before and after tool use and stop, running commands with JSON input
- [ ] Subagents with their own prompt, tools and model, for example a cheap model for search
- [ ] `gmi mcp login` to `mcp.gmicloud.ai/mcp`, which was in the original plan

**Done when:** a project defines a command, a hook and an MCP server. The
agent uses all three in a live session and each has offline tests.

### Phase 6: polish and reach (1.6)

- [ ] Status line with model, mode, context use, cost and git branch
- [ ] Themes for light and dark terminals, `NO_COLOR` support and an accessible plain mode
- [ ] Vim and Emacs key modes and a documented key reference
- [ ] Desktop notification when a long task finishes
- [ ] `install.ps1` for Windows, a Homebrew tap and standalone binaries for people without Python
- [ ] A documentation site with getting started, tools, permissions and recipes
- [ ] AgentBox marketplace listing for `gmi serve`, after the hackathon results

**Done when:** a new user installs with one command on macOS, Linux or Windows
and completes the getting-started guide without help.

## Principles for every phase

- **Live or it did not ship.** Offline tests guard behaviour. A live run proves it.
  Docs say exactly what was verified and what was not.
- **Safe by default.** The agent asks before writing, running or spending.
  Destructive actions and spending above a threshold always ask unless a rule allows them.
- **Keys never leave their place.** No key in project files, tool output, logs or sessions.
- **Scripts keep working.** Existing commands and `--json` output stay stable within 1.x.
- **Few dependencies.** Prefer the current stack (click, httpx, prompt_toolkit, rich)
  and pin exact versions.

## Open questions

- Which GMI models support tool calling and how they stream tool call deltas.
  Phase 2 answers this with live probes before phase 3 starts.
- Whether the Responses API (`POST /v1/responses`) suits the agent loop better
  than chat completions on GMI.
- Whether a compute-scope key can be created from the CLI for `--sandbox`, or
  users must create one in the console.
- Whether to keep Python or move to a single compiled binary. The plan keeps
  Python and ships binaries built from it in phase 6.

## How to help

Pick an unchecked item, open an issue saying you are working on it and follow
[CONTRIBUTING.md](../CONTRIBUTING.md). Small items in phases 1 and 2 are the best
place to start. Phase 3 changes need a short design note in the issue first.
