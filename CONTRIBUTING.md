# Contributing to gmi

Issues and pull requests are welcome. gmi is a community project for GMI Cloud.
[AGENTS.md](AGENTS.md) is the contract the coding agent follows. Human
contributors may read it for project context and verified API facts.

## Set up

Use Python 3.10 or newer. From a checkout, create a virtual environment and
install the test dependencies:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
```

The one-line installer defaults to tag `v1.0.1`. Set `GMI_REF` to test another
ref. See the [README Install section](README.md#install) for installer commands.

## Run tests

Run the offline suite before opening a pull request:

```sh
pytest -q -m "not live and not live_media"
```

The installer acceptance check uses Docker and two local images. It needs
network access only for PyPI dependencies:

```sh
sh tools/test_install_docker.sh
```

The `live` marker calls the real GMI API. The `live_media` marker makes real
media generations. Live tests need API keys and can cost money. Run them only
when you intend to call the service. Keep chat checks tiny with `max_tokens`
at most 16. Media checks should use one small generation per type with the
cheapest suitable model. The account queue is shared, so run media jobs one at
a time. Compute operations need a compute-scope key.

Never commit keys or tokens. Never paste them into issues, pull requests or
logs. Redact credentials from commands and output before sharing them.
Offline tests do not establish that an API operation works live. Describe
exactly what you tested and leave unverified areas explicit.

## Generated API commands

The vendored OpenAPI files in `specs/` are the source for
`src/gmi/api_manifest.json`. After a spec or generator change, regenerate and
check the result:

```sh
python tools/gen_api.py
python tools/gen_api.py --check
```

Commit the source change and regenerated manifest together. The `--check`
command must pass. Offline tests compare operations and resolved parameters
against the vendored specs. Do not edit only the generated manifest.

## Style

- Write plain, specific English in help text and documentation.
- Do not use em or en dashes in prose.
- Do not put a comma before "and" in prose.
- Keep dependencies few and pin exact versions.
- Preserve machine-readable JSON output and actionable errors on stderr.
- Keep existing limits and verification statements accurate.

## Report a bug or request a feature

Use the [bug report](https://github.com/zkasuran/gmi/issues/new?template=bug_report.yml)
or [feature request](https://github.com/zkasuran/gmi/issues/new?template=feature_request.yml)
form. For a bug, include `gmi --version`, your operating system, Python version,
the exact command and the expected and actual behaviour. Give the smallest
reproduction you can. Redact keys and tokens from every field and attachment.
For features, describe the problem and a concrete example of the command or
behaviour you want. Check the [README](README.md) and [docs](docs/) for known
limits first.

## Pull request checklist

- [ ] Explain the problem and resulting behaviour.
- [ ] Add meaningful tests for behaviour changes where needed.
- [ ] Run `pytest -q -m "not live and not live_media"` and report the result.
- [ ] Run `python tools/gen_api.py --check`.
- [ ] Include regenerated output when specs or the generator change.
- [ ] Update help text and documentation for user-visible changes.
- [ ] State any live verification and remaining limits precisely.
- [ ] Check the diff for credentials and remove them before sharing.
- [ ] Follow the prose style rules above.
