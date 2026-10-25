# Troubleshooting

Common errors, what causes them and how to fix them. Every entry restates the
[README](../README.md), [AGENTBOX.md](AGENTBOX.md) or [MCP.md](MCP.md).

## Sandbox or Cluster rejects your key (401 or 403)

**You see:** a sandbox command fails with a 401 or 403 response that includes
the compute key hint.

**Cause:** Sandbox and Cluster need a compute-scope key. The normal inference
key uses the `ie_model` scope and is rejected by Sandbox and Cluster.

**Fix:** Create a key in the GMI console under API keys and select the compute
scope. Set it in `GMI_COMPUTE_API_KEY` or its alias `GMI_SANDBOX_API_KEY`.

## Usage limits

| You see | Cause | Fix |
|---|---|---|
| `Daily limit reached` | This model has reached its UTC daily limit. | Try another model or wait for the reset time. |
| `Rate limit reached (429)` | You hit a quota. | Wait, or check your account limits. |

## `Sandbox.BillingNoPermission`

**You see:** the platform deletes the sandbox and gmi reports its message, code
and initiator:

```text
code: Sandbox.BillingNoPermission
initiator: billing
message: Deleted by the platform: billing order could not be created because the organization is not permitted to purchase this product
```

**Cause:** The billing order could not be created because the organization is
not permitted to purchase this product. Accounts lacking purchase permission
can encounter this failure.

**Fix:** The organization needs permission to purchase the product. For this
project's organization, GMI cleared the permission issue on 23 October.

## `MCP SDK missing`

**You see:** `gmi mcp serve` writes this to stderr and exits 2:

```text
MCP SDK missing; install with: pip install "gmi[mcp] @ git+https://github.com/zkasuran/gmi"
```

**Cause:** The MCP SDK is an optional extra. The core install can run
`gmi mcp tools` and `gmi mcp config` without it, but `gmi mcp serve` needs it.

**Fix:** Install gmi with the `mcp` extra (Python 3.10 or newer):

```sh
pip install "gmi[mcp] @ git+https://github.com/zkasuran/gmi"
pipx install "gmi[mcp] @ git+https://github.com/zkasuran/gmi"
uv tool install "gmi[mcp] @ git+https://github.com/zkasuran/gmi"
```

With the one-line installer, add `--mcp` (or set `GMI_INSTALL_MCP=1`):

```sh
curl -fsSL https://raw.githubusercontent.com/zkasuran/gmi/main/install.sh | sh -s -- --mcp
```
