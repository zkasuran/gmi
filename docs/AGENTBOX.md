# AgentBox and the gmi service

Day 4 adds sandbox commands and a small HTTP chat service. Implementation tests
are offline. Control plane schemas come from `specs/sandbox_api.yaml` with live
findings supplied by the project owner on 22 and 23 October 2026. The owner
verified sandbox run, create, exec, put, get-file, get, timeout and kill live on
23 October after GMI fixed the organization's billing permission. No marketplace
publication has happened.

## Sandbox credentials and data centres

The control plane is `https://console.gmicloud.ai/api/v2`. It requires a console
API key with compute scope in `Authorization: Bearer ...`. Use
`GMI_COMPUTE_API_KEY` or its alias `GMI_SANDBOX_API_KEY`. The shared Day 3
resolver also supports explicit `--api-key`, `GMI_TOKEN` and the normal key
fallback. An inference key with scope `ie_model` cannot authorize sandbox
operations. A missing key or a 401 or 403 response includes the compute key hint.

Data plane calls use `https://{sandbox_key}.{domain}` with `X-Access-Token`, never
the compute bearer key. Each exec or file operation calls `/connect` first and
uses its fresh host and sandbox token. Tokens are kept only in memory.

IDC selection is `--idc-name`, then `GMI_SANDBOX_IDC_NAME`, then
`gmi config set sandbox_idc NAME`. With none set the field is omitted so creation
can use the organization's default. The catalog may require an explicit IDC:
use one for `products`. If catalog lookup rejects an omitted IDC during creation,
gmi prints the general billing explanation and still lets creation use the
organization default.

The owner verified `us-central-iowa16` and `us-central-iowa2` for this organization.
The docs example `gmi-sandbox-us` does not exist. In `us-central-iowa16`,
`gmi.sandbox.small`, `gmi.sandbox.medium` and `gmi.sandbox.large` were active.
The x-small and x-large products were unavailable. Availability can change.
Custom resources are temporarily disabled by the platform and are not offered.

```sh
# Set the compute key in your environment privately first.
gmi sandbox products --idc-name us-central-iowa16
gmi config set sandbox_idc us-central-iowa16

gmi sandbox templates create --name demo --image ubuntu:24.04 \
  --product gmi.sandbox.small --cmd 'mkdir -p /home/user' \
  --start-cmd 'sleep infinity' --wait --timeout 600 --yes
gmi sandbox templates list
gmi sandbox templates get TEMPLATE_ID
gmi sandbox templates logs TEMPLATE_ID

gmi sandbox create TEMPLATE_ID --timeout 300 --env APP_ENV=demo --wait --yes
gmi sandbox list --state running
gmi sandbox get SANDBOX_ID
gmi sandbox timeout SANDBOX_ID 900 --yes
gmi sandbox exec SANDBOX_ID -- sh -c 'printf "hello\n"'
gmi sandbox put SANDBOX_ID ./input.txt /home/user/input.txt
gmi sandbox get-file SANDBOX_ID /home/user/input.txt --out ./output.txt
gmi sandbox kill SANDBOX_ID --yes
gmi sandbox templates delete TEMPLATE_ID --yes

gmi sandbox run --image ubuntu:24.04 --yes -- uname -a
```

The following stdout examples use offline test fixtures. Lists use tab-separated
columns and detail blocks stay within 10 lines. Add `--json` for the existing
machine-readable output. Template creation with `--wait` prints build progress
once per poll. Empty lists print `No templates.` or `No sandboxes.`
Build logs print one line per entry or `No build logs.` when empty.

```text
$ gmi sandbox templates create --name demo --yes
created: demo (template), build started

$ gmi sandbox templates create --name demo --wait --yes
building 0s
building 5s
building 10s
ready: demo (template) product gmi.sandbox.small in 17s

$ gmi sandbox templates list
name	status	product	idc	id
demo	ready	gmi.sandbox.small	us-central-iowa16	template

$ gmi sandbox templates get template
name: demo
id: template
status: ready
product: gmi.sandbox.small
vcpu: 2
memory_mb: 4096
idc: us-central-iowa16
created: 2026-10-22T12:00:00Z

$ gmi sandbox list
id	state	product	idc	ends
box	running	gmi.sandbox.small	us-central-iowa16	2026-10-22T12:05:00Z

$ gmi sandbox get box
id: box
state: running
product: gmi.sandbox.small
idc: us-central-iowa16
started: 2026-10-22T12:00:00Z
ends: 2026-10-22T12:05:00Z

$ gmi sandbox create template --yes
created sandbox box ends 2026-10-22T12:05:00Z

$ gmi sandbox templates logs template
line 0
line 1

$ gmi sandbox timeout box 900 --yes
timeout set: box ends 2026-10-22T12:15:00Z

$ gmi sandbox put box input.bin /home/user/data.bin --username user
uploaded input.bin to /home/user/data.bin

$ gmi sandbox get-file box /home/user/data.bin --out output.bin --username user
downloaded /home/user/data.bin to output.bin (13 bytes)

$ gmi sandbox kill box --yes
killing sandbox box (accepted)

$ gmi sandbox templates delete template --yes
deleting template template (accepted)
```

These are usage examples, not a record of live execution. The image must contain
the command you ask to run. Choose an appropriate image or add build commands
when creating a template.
All leaf commands accept `--json`. Creation, lifetime changes and deletion require
confirmation. Without a terminal or with `--json`, supply `--yes`.
File commands support `--username`; some IDCs ignore it so it is not a permission
isolation mechanism. Remote file paths must be absolute and their parent must
exist. Without `--out`, downloads use the remote file's basename locally.

Template waits poll every five seconds, stop on `ready` or `error` and default to
a 600 second deadline. On build error, gmi fetches the build failure message.
Sandbox `--timeout` sets the lifetime, not the wait deadline. `--wait-timeout`
sets the create wait deadline and defaults to 600 seconds. Creation prints the
sandbox ID and the service's `end_at`; it does not invent an expiry time.

Creation output masks sandbox access and traffic tokens by default, including
JSON. Only `sandbox create --json --show-token` reveals those tokens once with a secret
warning on stderr. Avoid that option in logs or shared terminals. Known control
and data plane credentials are redacted from other output, including exec output.
Keys are never printed. Build logs and command output may contain other private
data supplied by your workloads, so handle them privately.

Before a billable creation, gmi prints catalog price fields if present. Otherwise
it says billing is per second of runtime and covered by account credits where
applicable. It does not invent a rate, currency or price unit. The `products`
table has SKU, status, vCPU and memory in MB. It adds a price column only when the
response contains a price field.

`run` uses a deterministic template name for the image and product. It reuses a
ready template only after checking its build's image and product. Otherwise it
builds a template, creates a sandbox, waits for `running`, executes the command
and attempts to kill the sandbox in a `finally` block, including on Ctrl+C.
Templates remain for reuse. If sandbox cleanup fails, gmi reports the ID and the
manual kill command. Standalone `create` retains the resource on a wait timeout;
inspect it and kill it when finished. Exec prints stdout and stderr and returns
the command's exit code. Pending executions are polled until terminal with a
600 second deadline. A standalone exec timeout does not cancel the remote job.

## Known platform billing deletion

On 22 October 2026 the owner observed every sandbox being deleted by the
platform about one second after creation. GMI cleared this organization's billing
permission issue on 23 October and sandboxes now stay up. This deletion can still
happen when an account lacks purchase permission. The state became `deleting`
then `deleted` with:

```text
code: Sandbox.BillingNoPermission
initiator: billing
message: Deleted by the platform: billing order could not be created because the organization is not permitted to purchase this product
```

This is not a gmi error. For a deletion whose initiator is not `user`, gmi stops
on the first response showing deletion, prints the service's message with its
code and initiator and exits non-zero. It does not keep polling a deleted
sandbox. Offline regression tests cover this behaviour for create waits and run.
For initiator `user`, `gmi sandbox get ID` reports plain state with no error line
and exits 0. A wait interrupted by user deletion stops with the deletion reason
without calling it a platform deletion. This build did not request or obtain a
compute key and all tests in this update stay offline.

## HTTP service

`gmi serve` listens on `0.0.0.0:$PORT`, default 8080. It has two routes:

| Route | Request | Response |
|---|---|---|
| `GET /health` | No body or authentication required | `200 {"status":"ok"}` |
| `POST /chat` | `{"message":"Hello","model":"optional/model"}` | `{"model":"...","reply":"...","usage":{...}}` |

| Variable | Meaning |
|---|---|
| `PORT` | Listening port, default 8080. `--port` overrides it. |
| `GMI_MAAS_BASE_URL` | Runtime MaaS base URL injected by GMI. gmi appends `/v1` if absent. Local default: `https://api.gmi-serving.com`. |
| `GMI_MAAS_API_KEY` | Runtime MaaS key injected by GMI. Used only on the upstream request. |
| `GMI_API_KEY` | Local fallback when `GMI_MAAS_API_KEY` is absent. |
| `GMI_MODELS` | Default models as a comma-separated list, whitespace-separated list or JSON array. The first model is the default. |
| `GMI_SERVE_TOKEN` | Optional service bearer token. Every route except `/health` requires it when set. |

The request model overrides `GMI_MODELS`. Without either, the default is
`openai/gpt-6.1-sol`. No model allowlist is hard-coded. Select models available to
your MaaS account. The service makes non-streaming OpenAI-compatible requests to
`/v1/chat/completions` with a 30 second upstream timeout.

**Without `GMI_SERVE_TOKEN`, the service is open to anyone who can reach it.**
Those callers can make model calls billed to the configured account. The
AgentBox gateway's own access control is unverified here. Set a service token
when exposing the service and pass it as `Authorization: Bearer <token>` from
your client. Keep all keys and tokens in runtime secrets, never in the image.
`/health` checks the process only; it does not verify MaaS credentials or access.

```sh
# With runtime secrets already set privately:
gmi serve
curl http://localhost:8080/health
# This example uses the open local service. With a service token, add its bearer header.
curl http://localhost:8080/chat -H 'Content-Type: application/json' \
  -d '{"message":"Say hello","model":"openai/gpt-6.1-sol"}'
```

Invalid JSON or invalid fields return 400. Bodies above 64 KB return 413. Missing
upstream credentials return 503. Upstream HTTP errors, network failures, invalid
responses and timeouts return 502 with a short message. Raw upstream errors and
credentials are never returned. Requests are not logged.

## Build and publish an image

The Dockerfile installs the project on slim Python, runs as a non-root user and
exposes port 8080. Its health check calls `/health`. It starts `gmi serve`.
The build context excludes local config, environment files and repository history.

Run these steps yourself with a registry you control:

```sh
docker build -t YOUR_REGISTRY/gmi-agentbox:day4 .
docker push YOUR_REGISTRY/gmi-agentbox:day4
```

The owner verified a local Docker build and run on 22 October. Registry push
remains unverified. No registry credentials are available to this build.
For a local container test, pass secrets through the runtime environment:

```sh
docker run --rm -p 8080:8080 \
  -e GMI_API_KEY -e GMI_SERVE_TOKEN \
  YOUR_REGISTRY/gmi-agentbox:day4
```

After the hackathon results are announced, gmi will be deployed on the AgentBox marketplace; until then the Docker image has only been built and run locally.

## Image on GitHub Container Registry

The workflow in `.github/workflows/publish-image.yml` builds the repository
Dockerfile and publishes the service image as `ghcr.io/zkasuran/gmi`. A push of
a version tag matching `v*` triggers it. You can also run it manually from
GitHub Actions with the required `ref` input set to an existing version tag
such as `v1.0.2`. The workflow must be available on the default branch for
manual dispatch. It checks out that tag and rejects refs that are not version
tags. It uses only the built-in `GITHUB_TOKEN` for registry authentication.

For `v1.0.2` it publishes both `ghcr.io/zkasuran/gmi:v1.0.2` and
`ghcr.io/zkasuran/gmi:1.0.2`. It never publishes `latest`. Before any push, it
builds and runs the image without API keys, checks `/health`, checks that
unauthenticated `/chat` returns 401 and checks that logs contain no bearer
authorization marker. The container is then stopped. The workflow has not run
on GitHub yet and registry publication remains unverified.

After the first successful push, the package may need to be made public once
in GitHub: open the package's **Package settings**, then **Change visibility**
and choose public. GMI can then pull it without registry credentials. Confirm
the chosen version tag exists before registering it.

Use these values for **Path A: Host on GMI** in the
[AgentBox registration guide](https://docs.gmicloud.ai/agentbox-marketplace/register-an-agent):

| Field | Value |
|---|---|
| Project Name | `gmi` |
| Image URL | `ghcr.io/zkasuran/gmi:<version>`, for example `ghcr.io/zkasuran/gmi:1.0.2` after that tag is published. |
| Requires sign-in | Off for the public GHCR package. A private package requires registry credentials. |
| Data Center and Specification | Choose an available data center and instance type **Sandbox SMALL**. |
| Start Command | Leave empty to use the image default, `gmi serve`. |
| Public Access | Use protocol **HTTPS/2** with external port **443**. |
| Internal Port | `8080` |
| Environment Variables | Add `GMI_SERVE_TOKEN` with type **SECRET** and a strong private value. Keep the auto-generated `GMI_MAAS_API_KEY` supplied by GMI. |

`GMI_SERVE_TOKEN` protects `/chat` because the public address is reachable by
anyone. Without it, callers can spend the deployed account's model credits.
Clients must supply the matching bearer token. `/health` stays public. Never
bake the service token or API keys into the image. `GMI_MAAS_API_KEY` is
auto-generated by GMI and used by gmi for model calls; do not supply a personal
API key in the image or registration instructions.

Registering saves a template. Launching a sandbox and publishing a marketplace
listing are separate steps. Registering and publishing are not done yet and
have not been verified on the AgentBox console. After registration, launch a
sandbox and verify `/health`, authenticated `/chat` and runtime key injection
before requesting marketplace publication.

Choose the **Verified** path for an agent hosted on AgentBox with MaaS integration,
subject to GMI's review and current program requirements. This repository does
not claim that badge or a marketplace listing.

The alternative is a self-hosted **Powered by GMI MaaS** integration: run this
image on infrastructure you operate, set the MaaS base URL, key and models and
secure access with `GMI_SERVE_TOKEN`. GMI's review and branding approval are
external steps for that path too. Hosting this service yourself does not make it
an AgentBox Verified agent.

## Verification limits

Offline tests exercise HTTP contracts, auth separation, platform deletion,
build failures, token masking, confirmations, IDC precedence, command exit codes
and cleanup after command failure or Ctrl+C. Local fake-upstream tests exercise
`/health`, `/chat`, service auth, JSON validation, request size limits, credential
redaction and upstream failures. They inspect the configured timeout without
waiting 30 seconds.

Verified on 22 October: the live catalogue and template calls, a one-shot run that
stopped with the platform's billing message and a local Docker build and run of
the service image (non-root user, healthy, 401 without the token, a real chat
answered through GMI, no key in the logs).

Owner verified live on 23 October 2026 at about 09:30 IST with a compute key
from a clean install of main, after GMI fixed the organization's billing permission:

```text
$ gmi sandbox run --image ubuntu:24.04 --yes -- sh -c "echo hello-from-sandbox; uname -sr; id -un"
hello-from-sandbox
Linux 6.1.158+
root
```

The run exited 0 in 18 seconds and left no sandbox behind. The reusable template
stayed. The owner also verified these commands (ID, TEMPLATE, TIME, LOCAL and OUT
are placeholders for the reported values):

```text
$ gmi sandbox create TEMPLATE --timeout 240 --wait --yes
created sandbox ID ends TIME

$ gmi sandbox exec ID -- sh -c "echo from-exec; pwd"
from-exec
/

$ gmi sandbox exec ID -- sh -c "echo oops >&2; exit 3"
oops

$ gmi sandbox put ID LOCAL /tmp/in.txt
uploaded LOCAL to /tmp/in.txt

$ gmi sandbox get-file ID /tmp/in.txt --out OUT
downloaded /tmp/in.txt to OUT (22 bytes)

$ gmi sandbox timeout ID 1200 --yes
timeout set: ID ends TIME

$ gmi sandbox kill ID --yes
killing sandbox ID (accepted)
```

The failing exec printed `oops` on stderr and gmi exited 3.
`gmi sandbox exec ID -- cat /tmp/in.txt` returned the uploaded content and the
download was byte for byte identical. `gmi sandbox get ID` showed state `running`,
product `gmi.sandbox.small`, started and ends. The timeout command moved the end
time; without `--yes` and without a terminal it refused. After kill, `get` showed
state `deleted`, code `Sandbox.UserDeleted`, message `Deleted by user request`
and initiator `user`. No sandbox access token or traffic token appeared in output.
The offline-tested fix in this update makes user deletion plain state for `get`,
with no error line and exit code 0.

Still not verified: `gmi serve` on the AgentBox marketplace, including image
push, GMI injecting the runtime key, gateway access control and Verified approval.
Any Cluster write operation also remains unverified.

```sh
.venv/bin/pytest -q -m 'not live and not live_media'
.venv/bin/python tools/gen_api.py --check
```

## Day 4 validation record

On 22 October 2026, the completed implementation at `f7636d3` passed the full
offline suite twice:

```text
.venv/bin/pytest -q -m 'not live and not live_media'
Run 1: 141 passed, 9 deselected
Run 2: 141 passed, 9 deselected

.venv/bin/python tools/gen_api.py --check
Exit 0, generated manifest unchanged
```

This includes 81 new offline cases beyond the previous 60 tests. No new live
tests were added or run. `git diff --check` also passed. Docker build and push
were not run in that implementation session; the owner separately verified the
local Docker build and run on 22 October. Work stayed on `day4` without a merge or push.

Next: validate image push, AgentBox deployment, runtime key injection and gateway
access control before seeking marketplace approval. Cluster writes remain
unverified.

## 23 October offline update validation

The `day4d` update fixes user-deletion reporting and records the owner's live
verification and marketplace deployment plan. All checks in this session were
offline. The final suite ran twice:

```text
.venv/bin/pytest -q -m 'not live and not live_media'
Run 1: 196 passed, 9 deselected in 4.05s
Run 2: 196 passed, 9 deselected in 3.64s

.venv/bin/python tools/gen_api.py --check
Exit 0, generated manifest unchanged
```

The six added cases cover user deletion in text and JSON get output and stopping
create and run waits without platform wording. Existing platform-deletion cases
remain green. `git diff --check` passed. No key or live API was used. Work stayed
on `day4d` without a merge or push. The marketplace service, image push, runtime
key injection, gateway access control, Verified approval and Cluster writes
remain unverified. Next: follow the deployment plan after hackathon results and
validate those marketplace steps before seeking approval.
