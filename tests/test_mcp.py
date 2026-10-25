"""Offline MCP tests. The local HTTP server is a fixture, not live API proof."""
import asyncio
import builtins
from collections import Counter
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import threading
from uuid import UUID

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from click.testing import CliRunner
import httpx
import pytest

from gmi.cli import main
from gmi import mcp_server as mcp
from gmi.api_commands import manifest
from gmi.config import GMIError


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path))
    for key in ('GMI_API_KEY', 'GMI_TOKEN', 'GMI_COMPUTE_API_KEY', 'GMI_SANDBOX_API_KEY',
                'GMI_MCP_TEST_MODE', 'GMI_MCP_TEST_BASE'):
        monkeypatch.delenv(key, raising=False)


def operation(name):
    return next(op for op in manifest()['operations'] if mcp.tool_name(op) == name)


def test_names_unique_valid_and_stably_shortened():
    rows = mcp.generated_tools() + mcp.curated_tools()
    names = [row['name'] for row in rows]
    assert len(names) == len(set(names))
    assert all(re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name) for name in names)
    assert mcp.tool_name({'service': 'sandbox', 'name': 'get-sandbox'}) == 'sandbox_get_sandbox'
    long = {'service': 'cluster', 'name': 'a' * 100}
    assert len(mcp.tool_name(long)) == 64
    assert mcp.tool_name(long) == mcp.tool_name(dict(long))
    assert mcp.tool_name(long) != mcp.tool_name({**long, 'name': 'a' * 99 + 'b'})


def test_schemas_path_body_enum_and_annotations():
    import jsonschema
    for row in mcp.generated_tools() + mcp.curated_tools():
        jsonschema.Draft202012Validator.check_schema(row['inputSchema'])
        assert row['inputSchema']['additionalProperties'] is False
        assert row['annotations']['openWorldHint'] is True
    get = mcp.input_schema(operation('sandbox_get_sandbox'))
    assert get['properties']['id'] == {'type': 'string'}
    assert get['required'] == ['id']
    create = operation('sandbox_create_sandbox')
    schema = mcp.input_schema(create)
    assert 'Idempotency-Key' not in schema['properties']
    assert schema['properties']['body'] == create['request_body']['schema']
    assert 'body' in schema['required']
    assert 'template_id' in schema['properties']['body']['required']
    query = mcp.input_schema(operation('sandbox_get_template_build_logs'))
    assert query['properties']['level'] == {'type': 'string', 'enum': ['debug', 'info', 'warn', 'error']}
    rows = {row['name']: row for row in mcp.generated_tools()}
    assert rows['sandbox_get_sandbox']['annotations'] == {
        'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': True}
    assert rows['sandbox_delete_sandbox']['annotations']['destructiveHint'] is True
    assert rows['sandbox_delete_sandbox']['annotations']['idempotentHint'] is False


@pytest.mark.parametrize('flags,methods,curated', [
    ([], {'GET'}, {'gmi_models', 'gmi_chat', 'gmi_sandbox_get_file'}),
    (['--allow-write'], {'GET', 'POST', 'PUT', 'PATCH'},
     {'gmi_models', 'gmi_chat', 'gmi_sandbox_get_file', 'gmi_sandbox_run', 'gmi_sandbox_exec', 'gmi_sandbox_put_file'}),
    (['--allow-destructive'], {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'},
     {'gmi_models', 'gmi_chat', 'gmi_sandbox_get_file', 'gmi_sandbox_run', 'gmi_sandbox_exec', 'gmi_sandbox_put_file'}),
])
def test_gating_and_exact_cli_counts_from_manifest(flags, methods, curated):
    # Independent policy oracle: do not use the implementation's eligible or select_tools.
    allowed = [op for op in manifest()['operations']
               if op['security_scheme'] not in {'sandboxAccessToken', 'bearerEmailVerificationToken'}
               and op['name'] != 'create-api-key'
               and not (op['service'] == 'iam' and op['group'] == 'oauth')
               and op['method'] in methods]
    result = CliRunner().invoke(main, ['mcp', 'tools', '--json', *flags])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    expected_names = {op['service'] + '_' + op['name'].replace('-', '_') for op in allowed} | curated
    assert {row['name'] for row in report['tools']} == expected_names
    counts = Counter('read' if op['method'] == 'GET' else 'destructive' if op['method'] == 'DELETE' else 'write' for op in allowed)
    counts['read'] += 3
    counts['write'] += len(curated) - 3
    assert report['counts'] == {**{kind: counts[kind] for kind in ('read', 'write', 'destructive')},
                                'curated': len(curated), 'total': len(expected_names)}
    for op in manifest()['operations']:
        if op['security_scheme'] in {'sandboxAccessToken', 'bearerEmailVerificationToken'} or op['name'] == 'create-api-key' or (op['service'] == 'iam' and op['group'] == 'oauth'):
            assert mcp.tool_name(op) not in expected_names


def test_service_and_glob_filters():
    result = CliRunner().invoke(main, ['mcp', 'tools', '--json', '--services', 'sandbox',
        '--include', 'sandbox_get_*', '--include', 'gmi_*', '--exclude', '*file*'])
    assert result.exit_code == 0
    names = {row['name'] for row in json.loads(result.output)['tools']}
    assert 'gmi_models' in names and 'gmi_chat' in names and 'sandbox_get_sandbox' in names
    assert not any('file' in name for name in names)
    assert not any(name.startswith('iam_') for name in names)
    assert CliRunner().invoke(main, ['mcp', 'tools', '--services', 'typo']).exit_code == 2


def test_auto_idempotency_and_shared_key_selection(monkeypatch):
    monkeypatch.setenv('GMI_API_KEY', 'fake-inference')
    monkeypatch.setenv('GMI_COMPUTE_API_KEY', 'fake-compute')
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'id': 'created'})
    original = httpx.Client
    monkeypatch.setattr(mcp.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    for _ in range(2):
        value, error = mcp.execute_operation(operation('sandbox_create_sandbox'), {'body': {'template_id': 't'}}, 17)
        assert value == {'id': 'created'} and not error
    ids = [str(UUID(request.headers['Idempotency-Key'])) for request in calls]
    assert ids[0] != ids[1]
    assert all(request.headers['Authorization'] == 'Bearer fake-compute' for request in calls)
    assert all(request.headers['User-Agent'] == 'gmi-mcp/' + mcp.__version__ for request in calls)
    assert all(request.extensions['timeout']['read'] == 17 for request in calls)


@pytest.mark.parametrize('status', [401, 403, 429, 500])
def test_http_errors_preserve_service_message_and_scope_hint(monkeypatch, status):
    monkeypatch.setenv('GMI_COMPUTE_API_KEY', 'fake-compute')
    original = httpx.Client
    monkeypatch.setattr(mcp.httpx, 'Client', lambda **kw: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json={'message': 'service says no'})), **kw))
    value, error = mcp.execute_operation(operation('sandbox_get_sandbox'), {'id': 'box'}, 60)
    assert error and value['status'] == status and value['message']['message'] == 'service says no'
    if status in (401, 403):
        assert 'GMI_COMPUTE_API_KEY' in value['hint'] and 'compute scope' in value['hint']


@pytest.mark.parametrize('name,redacted', [
    ('token', True), ('secret', True), ('password', True), ('passwd', True),
    ('api_key', True), ('apikey', True), ('authorization', True),
    ('credential', True), ('credentials', True), ('private_key', True), ('key', True),
    ('sandbox_access_token', True), ('traffic_access_token', True),
    ('access_token', True), ('refresh_token', True), ('id_token', True),
    ('clientSecret', True), ('client_passwd', True), ('proxy_authorization', True),
    ('service_credential', True), ('service_credentials', True), ('ssh_private_key', True),
    ('prompt_tokens', False), ('completion_tokens', False), ('total_tokens', False),
    ('max_tokens', False), ('output_tokens', False), ('tokens', False),
    ('token_count', False), ('secret_label', False), ('password_hint', False),
    ('api_key_count', False), ('key_name', False), ('monkey', False),
])
def test_redaction_credential_key_rule(name, redacted):
    # Use the same harmless string in each case to distinguish names from values.
    for key in (name, name.upper(), name.upper().replace('_', '-')):
        for value in ('sample-value', 16, 1.5, True, False, None, {'count': 3}, [4]):
            expected = '[redacted]' if redacted and isinstance(value, str) else value
            payload = {key: value, 'nested': [{key: value}]}
            cleaned = {key: expected, 'nested': [{key: expected}]}
            assert mcp.redact(payload) == cleaned
            assert mcp.sensitive_values(payload) == ({value} if expected == '[redacted]' else set())
            result = mcp.hygienic_result(payload)
            assert result['structuredContent'] == cleaned
            assert json.loads(result['content'][0]['text']) == cleaned
            embedded = mcp.hygienic_result({'json_text': json.dumps(payload)})
            assert json.loads(embedded['structuredContent']['json_text']) == cleaned


def test_redaction_recurses_through_credential_named_containers():
    payload = {'access_token': [{'password': 'sample-value', 'total_tokens': 16}],
               'secret': {'api_key': 'other-value', 'max_tokens': True}}
    expected = {'access_token': [{'password': '[redacted]', 'total_tokens': 16}],
                'secret': {'api_key': '[redacted]', 'max_tokens': True}}
    assert mcp.hygienic_result(payload)['structuredContent'] == expected


def test_redaction_nested_keys_jwt_and_known_credentials():
    jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJl'
    payload = {'sandbox_access_token': 'fake-access', 'nested': [{'API_KEY': 'fake-key', 'clientSecret': 'secret',
               'PASSWORD': 'password', 'reply': 'Bearer ' + jwt, 'echo': 'fake-compute'}],
               'json_text': '{"password":"hidden"}'}
    result = mcp.hygienic_result(payload, secrets=['fake-compute'])
    rendered = json.dumps(result)
    for secret in ('fake-access', 'fake-key', 'fake-compute', 'secret"', 'password"', jwt, 'hidden'):
        assert secret not in rendered
    assert result['structuredContent']['sandbox_access_token'] == '[redacted]'
    assert result['structuredContent']['nested'][0]['reply'] == 'Bearer [redacted]'
    assert '[redacted]' in rendered


@pytest.mark.parametrize('value', [{'long': 'é' * 5000}, ['x' * 5000], 'x' * 5000])
def test_truncation_bounds_utf8_and_drops_unbounded_structured_content(value):
    result = mcp.hygienic_result(value, max_output_bytes=256)
    text = result['content'][0]['text']
    assert len(text.encode('utf-8')) <= 256
    assert re.search(r'\[truncated \d+ bytes\]$', text)
    assert result['structuredContent'] is None
    prefix, marker = text.rsplit('\n', 1)
    removed = int(re.search(r'\d+', marker).group())
    assert removed == len(json.dumps(value, ensure_ascii=False).encode()) - len(prefix.encode())


def test_test_base_requires_both_variables(monkeypatch):
    monkeypatch.setenv('GMI_MCP_TEST_BASE', 'http://127.0.0.1:1')
    assert mcp.test_base('https://real.example') == 'https://real.example'
    monkeypatch.setenv('GMI_MCP_TEST_MODE', '0')
    assert mcp.test_base('https://real.example') == 'https://real.example'
    monkeypatch.setenv('GMI_MCP_TEST_MODE', '1')
    assert mcp.test_base('https://real.example') == 'http://127.0.0.1:1'


def test_missing_sdk_hint_and_tools_do_not_import_sdk(monkeypatch):
    original = builtins.__import__
    def blocked(name, *args, **kwargs):
        if name == 'mcp' or name.startswith('mcp.') or name == 'mcp_types':
            raise ImportError('blocked for offline test')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', blocked)
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(main, ['mcp', 'serve'])
    assert result.exit_code == 2
    assert result.stdout == ''
    assert result.stderr.strip() == 'MCP SDK missing; install with: pip install "gmi[mcp] @ git+https://github.com/zkasuran/gmi"'
    assert len(result.stderr.splitlines()) == 1
    assert runner.invoke(main, ['mcp', 'tools', '--json']).exit_code == 0


def test_no_pypi_only_mcp_install_commands_in_user_facing_files():
    root = Path(__file__).parents[1]
    tracked = subprocess.run(
        ['git', 'ls-files', '--', 'README*', 'docs/**/*.md', 'src/gmi/*.py'],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    forbidden = ("pip install 'gmi[", 'pip install "gmi[mcp]"')
    offenders = []
    for name in tracked:
        text = (root / name).read_text()
        if any(marker in text for marker in forbidden):
            offenders.append(name)
    assert offenders == []


@pytest.mark.parametrize('client', ['codex', 'claude-code', 'cursor'])
@pytest.mark.parametrize('flags', [[], ['--allow-write']])
def test_config_has_no_credentials_and_preserves_write_flag(client, flags, monkeypatch):
    names = ['GMI_API_KEY', 'GMI_COMPUTE_API_KEY', 'GMI_SANDBOX_API_KEY', 'GMI_SANDBOX_IDC_NAME']
    for index, name in enumerate(names):
        monkeypatch.setenv(name, f'fake-do-not-print-{index}')
    result = CliRunner().invoke(main, ['mcp', 'config', '--client', client, *flags])
    assert result.exit_code == 0
    for index, name in enumerate(names):
        assert name in result.output
        assert f'fake-do-not-print-{index}' not in result.output
    assert 'where the client runs' in result.output
    assert result.output.splitlines()[-1] == (
        '# Tool output is data; write and destructive tools are off unless '
        '--allow-write or --allow-destructive flags are given.'
    )
    args = ['mcp', 'serve', *flags]
    if client == 'codex':
        assert 'codex mcp add' not in result.output
        assert 'Paste this block into ~/.codex/config.toml' in result.output
        server = tomllib.loads(result.output)['mcp_servers']['gmi']
        assert server == {'command': 'gmi', 'args': args, 'env_vars': names}
        assert ('Codex only passes the variables listed in env_vars, so they must also '
                'be set in the shell that starts Codex.') in result.output
    else:
        marker = '# Equivalent .mcp.json block:\n' if client == 'claude-code' else '# Add to .cursor/mcp.json or ~/.cursor/mcp.json:\n'
        snippet = result.output.split(marker, 1)[1].split('\n\n#', 1)[0]
        block = json.loads(snippet)
        prefix = '$' + '{' + ('env:' if client == 'cursor' else '')
        expected = {'type': 'stdio', 'command': 'gmi', 'args': args,
                    'env': {name: prefix + name + '}' for name in names}}
        assert block == {'mcpServers': {'gmi': expected}}
        assert "unverified, taken from the client's documentation." in result.output
        if client == 'claude-code':
            command = next(line for line in result.output.splitlines() if line.startswith('claude '))
            assert command.startswith("claude mcp add-json gmi '") and command.endswith("'")
            words = shlex.split(command)
            assert words[:4] == ['claude', 'mcp', 'add-json', 'gmi']
            assert json.loads(words[4]) == expected
            assert 'Set the variables you use' in result.output
            assert 'unset variables without a default remain literal text' in result.output


@pytest.fixture
def local_cloud():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            calls.append((self.path, dict(self.headers)))
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert self.path == '/chat/completions'
            assert body['max_completion_tokens'] == 16
            payload = {'choices': [{'message': {'content': 'reply'}}],
                       'usage': {'prompt_tokens': 5, 'completion_tokens': 3, 'total_tokens': 8}}
            response = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response)))
            self.end_headers()
            self.wfile.write(response)
        def do_GET(self):
            calls.append((self.path, dict(self.headers)))
            payload = {'id': 'box', 'state': 'running', 'sandbox_access_token': 'fake-data-secret',
                       'message': 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJl'}
            body = json.dumps(payload).encode()
            self.send_response(403 if self.path.endswith('/rejected') else 200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize('mode,version', [('2026-07-28', '2026-07-28'), ('legacy', '2025-11-25')])
def test_official_client_stdio_protocol_and_stdout_hygiene(local_cloud, tmp_path, monkeypatch, mode, version):
    from mcp.client import Client
    from mcp.shared.exceptions import MCPError
    from mcp.client.stdio import StdioServerParameters, stdio_client
    stdio_module = importlib.import_module('mcp.client.stdio')
    # Observe every complete stdout line at the official client's parser boundary.
    original_parse = stdio_module._parse_line
    stdout_lines = []
    def record(line):
        stdout_lines.append(line)
        return original_parse(line)
    monkeypatch.setattr(stdio_module, '_parse_line', record)
    base, calls = local_cloud
    params = StdioServerParameters(command=sys.executable,
        args=['-c', 'from gmi.cli import main; main()', 'mcp', 'serve'],
        env={'HOME': str(tmp_path), 'GMI_COMPUTE_API_KEY': 'fake-protocol-key',
             'GMI_API_KEY': 'fake-protocol-inference',
             'GMI_MCP_TEST_MODE': '1', 'GMI_MCP_TEST_BASE': base})
    stderr_path = tmp_path / 'server.stderr'
    async def exercise():
        with stderr_path.open('w') as errlog:
            @asynccontextmanager
            async def transport():
                async with stdio_client(params, errlog=errlog) as streams:
                    yield streams
            async with Client(transport(), mode=mode, read_timeout_seconds=15, cache=None) as client:
                assert client.protocol_version == version
                listed = await client.list_tools()
                names = {tool.name for tool in listed.tools}
                assert 'sandbox_get_sandbox' in names
                assert 'sandbox_create_sandbox' not in names
                assert names == {row['name'] for row in mcp.select_tools()}
                result = await client.call_tool('sandbox_get_sandbox', {'id': 'a/b'})
                assert not result.is_error
                assert result.structured_content['id'] == 'box'
                assert result.structured_content['sandbox_access_token'] == '[redacted]'
                assert result.structured_content['message'] == '[redacted]'
                chat = await client.call_tool('gmi_chat', {'message': 'hello', 'max_tokens': 16})
                assert not chat.is_error
                expected = {'reply': 'reply', 'usage': {
                    'prompt_tokens': 5, 'completion_tokens': 3, 'total_tokens': 8}}
                assert chat.structured_content == expected
                assert json.loads(chat.content[0].text) == expected
                with pytest.raises(MCPError, match='--allow-write') as gated:
                    await client.call_tool('sandbox_create_sandbox', {'body': {'template_id': 't'}})
                assert gated.value.code == -32602
                with pytest.raises(MCPError, match='--allow-destructive'):
                    await client.call_tool('sandbox_delete_sandbox', {'id': 'box'})
                bad = await client.call_tool('sandbox_get_sandbox', {'id': 123})
                assert bad.is_error and 'Invalid tool arguments' in bad.content[0].text
                extra = await client.call_tool('sandbox_get_sandbox', {'id': 'box', 'surprise': True})
                assert extra.is_error
                capped = await client.call_tool('gmi_chat', {'message': 'hello', 'max_tokens': 1025})
                assert capped.is_error
                rejected = await client.call_tool('sandbox_get_sandbox', {'id': 'rejected'})
                assert rejected.is_error and rejected.structured_content['status'] == 403
                assert 'GMI_COMPUTE_API_KEY' in rejected.content[0].text
                assert 'fake-data-secret' not in rejected.content[0].text
    asyncio.run(exercise())
    assert len(calls) == 3
    assert calls[0][0] == '/sandboxes/a%2Fb'
    assert calls[0][1]['Authorization'] == 'Bearer fake-protocol-key'
    assert calls[0][1]['User-Agent'] == 'gmi-mcp/' + mcp.__version__
    assert stdout_lines
    for line in stdout_lines:
        parsed = json.loads(line)
        assert parsed['jsonrpc'] == '2.0'
        assert ('result' in parsed) or ('error' in parsed) or ('method' in parsed)
    all_output = '\n'.join(stdout_lines) + stderr_path.read_text()
    assert 'fake-protocol-key' not in all_output and 'fake-data-secret' not in all_output
    assert 'fake-protocol-inference' not in all_output
    assert 'eyJhbGciOiJIUzI1NiJ9' not in all_output


@pytest.fixture
def sandbox_cloud(monkeypatch):
    from gmi import sandbox
    monkeypatch.setenv('GMI_API_KEY', 'fake-inference-key')
    monkeypatch.setenv('GMI_COMPUTE_API_KEY', 'fake-compute-key')
    state = {'calls': [], 'connections': 0, 'content': b'hello', 'exec_failure': False}
    def handler(request):
        state['calls'].append(request)
        assert request.headers['User-Agent'] == 'gmi-mcp/' + mcp.__version__
        if request.url.host == 'box.example.test':
            assert 'Authorization' not in request.headers
            assert request.headers['X-Access-Token'] == f"fake-access-{state['connections']}"
            if request.url.path == '/executions':
                if state['exec_failure']:
                    return httpx.Response(500, json={'error': 'command rejected'})
                assert json.loads(request.content)['parameters']['command'] == 'echo hello'
                return httpx.Response(200, json={'stdout': 'hello fake-access-1', 'stderr': '', 'exit_code': 3, 'status': 'completed'})
            assert request.url.path == '/files'
            assert request.url.params['path'] == '/tmp/data.txt'
            if request.method == 'GET':
                return httpx.Response(200, content=state['content'])
            assert b'hello' in request.content
            return httpx.Response(200, json={'path': '/tmp/data.txt', 'sandbox_access_token': 'fake-access-1'})
        assert request.headers['Authorization'] == 'Bearer fake-compute-key'
        assert 'X-Access-Token' not in request.headers
        path = request.url.path.removeprefix('/api/v2')
        if path == '/templates':
            if request.method == 'GET':
                return httpx.Response(200, json={'items': [], 'total': 0})
            return httpx.Response(200, json={'id': 'template'})
        if path == '/templates/template':
            return httpx.Response(200, json={'latest_build_status': 'ready'})
        if path == '/sandboxes' and request.method == 'POST':
            assert json.loads(request.content)['timeout'] <= 300
            return httpx.Response(200, json={'id': 'box'})
        if path == '/sandboxes/box/connect':
            state['connections'] += 1
            return httpx.Response(200, json={'sandbox_key': 'box', 'domain': 'example.test',
                'sandbox_access_token': f"fake-access-{state['connections']}"})
        if path == '/sandboxes/box':
            return httpx.Response(200, json={'state': 'running', 'id': 'box'})
        raise AssertionError('Unexpected fake sandbox path')
    monkeypatch.setattr(sandbox, 'HTTPTransport', lambda: httpx.MockTransport(handler))
    return state


def test_curated_sandbox_run_exec_and_fresh_internal_connections(sandbox_cloud):
    state = sandbox_cloud
    run = mcp.execute_curated('gmi_sandbox_run', {'image': 'ubuntu:24.04', 'command': ['echo', 'hello'], 'timeout': 45}, 13)
    assert run == {'stdout': 'hello [redacted]', 'stderr': '', 'exit_code': 3}
    assert any(r.method == 'DELETE' and r.url.path.endswith('/sandboxes/box') for r in state['calls'])
    execute = mcp.execute_curated('gmi_sandbox_exec', {'sandbox_id': 'box', 'command': ['echo', 'hello']}, 13)
    assert execute['exit_code'] == 3
    assert state['connections'] == 2
    assert all(r.extensions['timeout']['read'] == 13 for r in state['calls'])
    assert not any('fake-access' in str(v) for v in run.values())


def test_curated_sandbox_run_cleanup_on_execution_failure(sandbox_cloud):
    state = sandbox_cloud
    state['exec_failure'] = True
    with pytest.raises(GMIError, match='The request did not complete and the change may or may not have been made'):
        mcp.execute_curated('gmi_sandbox_run', {'image': 'ubuntu:24.04', 'command': ['echo', 'hello']}, 60)
    assert state['calls'][-1].method == 'DELETE'
    assert state['calls'][-1].url.path.endswith('/sandboxes/box')


def test_curated_file_roundtrip_limits_and_no_host_file_access(sandbox_cloud):
    put = mcp.execute_curated('gmi_sandbox_put_file', {'sandbox_id': 'box', 'path': '/tmp/data.txt', 'content': 'hello'}, 60)
    assert put['sandbox_access_token'] == '[redacted]'
    get = mcp.execute_curated('gmi_sandbox_get_file', {'sandbox_id': 'box', 'path': '/tmp/data.txt'}, 60)
    assert get == {'content': 'hello', 'bytes': 5}
    assert sandbox_cloud['connections'] == 2
    sandbox_cloud['content'] = b'x' * 262145
    with pytest.raises(GMIError, match='256 KB'):
        mcp.execute_curated('gmi_sandbox_get_file', {'sandbox_id': 'box', 'path': '/tmp/data.txt'}, 60)
    sandbox_cloud['content'] = b'\xff\xfe'
    with pytest.raises(GMIError, match='Binary file omitted'):
        mcp.execute_curated('gmi_sandbox_get_file', {'sandbox_id': 'box', 'path': '/tmp/data.txt'}, 60)
    with pytest.raises(GMIError, match='256 KB'):
        mcp.execute_curated('gmi_sandbox_put_file', {'sandbox_id': 'box', 'path': '/tmp/data.txt', 'content': 'é' * 131073}, 60)


def test_curated_inference_key_models_search_and_chat_usage(monkeypatch):
    monkeypatch.setenv('GMI_API_KEY', 'fake-inference')
    monkeypatch.setenv('GMI_COMPUTE_API_KEY', 'fake-compute')
    requests = []
    def handler(request):
        requests.append(request)
        assert request.headers['Authorization'] == 'Bearer fake-inference'
        assert request.headers['User-Agent'] == 'gmi-mcp/' + mcp.__version__
        if request.url.path.endswith('/models'):
            return httpx.Response(200, json={'data': [{'id': 'a/first'}, {'id': 'b/second'}, {'id': 'a/third'}]})
        assert request.url.path.endswith('/chat/completions')
        body = json.loads(request.content)
        assert body['messages'] == [{'role': 'user', 'content': 'hello'}]
        assert body['max_completion_tokens'] == 16
        return httpx.Response(200, json={'choices': [{'message': {'content': 'reply'}}], 'usage': {'total_tokens': 16}})
    original = httpx.Client
    monkeypatch.setattr(mcp.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    result = mcp.execute_curated('gmi_models', {'search': 'a/', 'limit': 1}, 60)
    assert result == {'models': [{'id': 'a/first'}], 'total': 2}
    result = mcp.execute_curated('gmi_chat', {'message': 'hello', 'max_tokens': 16}, 60)
    assert result == {'reply': 'reply', 'usage': {'total_tokens': 16}}
    assert len(requests) == 2


@pytest.mark.parametrize('name,args', [
    ('gmi_chat', {'message': 'hello', 'max_tokens': 1025}),
    ('gmi_sandbox_run', {'image': 'ubuntu', 'command': ['true'], 'timeout': 301}),
    ('gmi_sandbox_exec', {'sandbox_id': 'box', 'command': []}),
    ('gmi_sandbox_get_file', {'sandbox_id': 'box', 'path': '/tmp/a', 'access_token': 'never-accept'}),
])
def test_curated_argument_safety_constraints(name, args):
    import jsonschema
    schema = next(row['inputSchema'] for row in mcp.curated_tools() if row['name'] == name)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(args, schema)


def test_returned_credentials_are_also_removed_from_echoed_fields():
    result = mcp.hygienic_result({'sandbox_access_token': 'fake-new-token',
                                 'message': 'echo fake-new-token', 'nested': {'password': 'private-value'},
                                 'copy': 'private-value'})
    assert result['structuredContent']['message'] == 'echo [redacted]'
    assert result['structuredContent']['copy'] == '[redacted]'


def test_no_manifest_body_still_allows_an_optional_free_object():
    schema = mcp.input_schema(operation('sandbox_get_sandbox'))
    assert schema['properties']['body'] == {'type': 'object'}
    assert 'body' not in schema['required']


def test_idempotency_schema_matches_manifest_and_retry_descriptions():
    expected = {mcp.tool_name(op) for op in manifest()['operations']
                if any(p['name'].lower() == 'idempotency-key' for p in op['header_parameters'])}
    rows = mcp.generated_tools() + mcp.curated_tools()
    actual = {row['name'] for row in rows if 'idempotency_key' in row['inputSchema']['properties']}
    assert actual == expected
    assert len(actual) == 2
    for row in rows:
        if row['name'] in expected:
            schema = row['inputSchema']
            assert 'idempotency_key' not in schema['required']
            assert schema['properties']['idempotency_key']['description'] == (
                'Reuse the same value when retrying this call so GMI can recognise a duplicate. '
                'If omitted, gmi generates one and returns it in the result metadata.')
            assert 'Pass the same idempotency_key when retrying.' in row['description']
        elif row['class'] != 'read':
            assert ('Not idempotent unless it has an idempotency_key argument: '
                    'check state before retrying.') in row['description']
        if row['class'] != 'read':
            assert row['annotations']['idempotentHint'] is False
    run = next(row for row in rows if row['name'] == 'gmi_sandbox_run')
    assert 'may have left a running sandbox or a template' in run['description']
    assert 'sandbox_list_sandboxes' in run['description']


@pytest.fixture
def idempotency_cloud():
    state = {'mode': 'success', 'calls': []}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def handle_request(self):
            if self.headers.get('Content-Length'):
                self.rfile.read(int(self.headers['Content-Length']))
            state['calls'].append((self.command, self.path, dict(self.headers)))
            mode = state['mode']
            if mode == 'network':
                self.close_connection = True
                return
            if mode == 'timeout':
                threading.Event().wait(0.3)
                self.close_connection = True
                return
            body = json.dumps({'id': 'created', 'message': 'service response'}).encode()
            self.send_response(int(mode) if mode.isdigit() else 200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        do_POST = do_GET = do_DELETE = handle_request
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize('protocol_mode', ['2026-07-28', 'legacy'])
def test_idempotency_over_official_client_and_fake_http_server(idempotency_cloud, tmp_path, protocol_mode):
    from mcp.client import Client
    from mcp.client.stdio import StdioServerParameters, stdio_client
    base, state = idempotency_cloud
    params = StdioServerParameters(command=sys.executable,
        args=['-c', 'from gmi.cli import main; main()', 'mcp', 'serve',
              '--allow-destructive', '--timeout', '0.1'],
        env={'HOME': str(tmp_path), 'GMI_COMPUTE_API_KEY': 'fake-compute',
             'GMI_MCP_TEST_MODE': '1', 'GMI_MCP_TEST_BASE': base})
    keyed = [op for op in manifest()['operations']
             if any(p['name'].lower() == 'idempotency-key' for p in op['header_parameters'])]
    bodies = {
        '/sandboxes': {'template_id': 't'},
        '/templates': {'name': 'test', 'resources': {'type': 'preset', 'product': 'gmi.sandbox.small'}},
    }
    warning = ('The request did not complete and the change may or may not have been made. '
               'Check the current state with a list or get tool before retrying.')
    async def exercise():
        @asynccontextmanager
        async def transport():
            async with stdio_client(params) as streams:
                yield streams
        async with Client(transport(), mode=protocol_mode, read_timeout_seconds=15, cache=None) as client:
            async def call(name, arguments, mode='success', sent=True):
                state['mode'] = mode
                before = len(state['calls'])
                result = await client.call_tool(name, arguments)
                assert len(state['calls']) == before + int(sent)
                return result

            for op in keyed:
                name = mcp.tool_name(op)
                arguments = {'body': bodies[op['path']]}
                supplied = 'retry.Same_123-abc'
                for _ in range(2):
                    result = await call(name, {**arguments, 'idempotency_key': supplied})
                    assert not result.is_error
                    assert result.meta['gmi/idempotency_key'] == supplied
                    assert state['calls'][-1][2]['Idempotency-Key'] == supplied
                result = await call(name, arguments)
                generated = result.meta['gmi/idempotency_key']
                assert str(UUID(generated)) == generated
                assert state['calls'][-1][2]['Idempotency-Key'] == generated
                for invalid in ('', 'short', 'x' * 129, 'has spaces', 'nonascii-é', 'linebreak\n', 123, None):
                    result = await call(name, {**arguments, 'idempotency_key': invalid}, sent=False)
                    assert result.is_error
                    assert ('8 to 128 characters using only letters, digits, dot, underscore and hyphen'
                            in result.content[0].text)
                for boundary in ('x' * 8, 'x' * 128):
                    result = await call(name, {**arguments, 'idempotency_key': boundary})
                    assert not result.is_error
                    assert state['calls'][-1][2]['Idempotency-Key'] == boundary
                for mode in ('network', 'timeout', '500', '503'):
                    for key_args in ({}, {'idempotency_key': supplied}):
                        result = await call(name, {**arguments, **key_args}, mode)
                        assert result.is_error
                        used = result.meta['gmi/idempotency_key']
                        assert state['calls'][-1][2]['Idempotency-Key'] == used
                        if key_args:
                            assert used == supplied
                        else:
                            assert str(UUID(used)) == used
                        text = result.content[0].text
                        assert warning in text
                        assert f'To retry safely, call again with idempotency_key set to {used}.' in text
                result = await call(name, arguments, '400')
                assert result.is_error
                assert result.meta['gmi/idempotency_key'] == state['calls'][-1][2]['Idempotency-Key']
                # Failures before HTTP still retain the prepared key.
                result = await call(name, {'idempotency_key': supplied}, sent=False)
                assert result.is_error and result.meta['gmi/idempotency_key'] == supplied

            for name, arguments in (
                    ('sandbox_set_sandbox_timeout', {'id': 'box', 'body': {'timeout': 60}}),
                    ('sandbox_delete_sandbox', {'id': 'box'})):
                for mode in ('network', 'timeout', '500'):
                    result = await call(name, arguments, mode)
                    assert result.is_error
                    assert warning in result.content[0].text
                    assert 'To retry safely' not in result.content[0].text
                    assert 'gmi/idempotency_key' not in (result.meta or {})
            result = await call('sandbox_get_sandbox', {'id': 'box'}, 'network')
            assert result.is_error
            assert 'check connectivity and retry.' in result.content[0].text
            assert warning not in result.content[0].text
    asyncio.run(exercise())


def test_curated_run_failure_warns_about_leftovers_without_credentials(tmp_path):
    from mcp.client import Client
    from mcp.client.stdio import StdioServerParameters, stdio_client
    params = StdioServerParameters(command=sys.executable,
        args=['-c', 'from gmi.cli import main; main()', 'mcp', 'serve', '--allow-write'],
        env={'HOME': str(tmp_path)})
    async def exercise():
        @asynccontextmanager
        async def transport():
            async with stdio_client(params) as streams:
                yield streams
        async with Client(transport(), read_timeout_seconds=15, cache=None) as client:
            result = await client.call_tool('gmi_sandbox_run', {'image': 'ubuntu', 'command': ['true']})
            assert result.is_error
            assert 'may have left a running sandbox or a template' in result.content[0].text
            assert 'sandbox_list_sandboxes' in result.content[0].text
    asyncio.run(exercise())
