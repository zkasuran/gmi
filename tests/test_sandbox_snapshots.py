"""Offline contract tests for snapshots, pause and resume. Not verified live."""
import json

import httpx
import pytest
from click.testing import CliRunner

from gmi import sandbox
from gmi.cli import main

COMPUTE = 'fake-compute-credential-112233'
ACCESS = 'fake-sandbox-secret-445566'
SNAP = '11111111-2222-4333-8444-555555555555'
BOX = '99999999-8888-4777-8666-555555555555'

SNAPSHOT = {
    'snapshot_id': SNAP, 'idc_name': 'us-central-iowa16', 'name': 'with-jq', 'status': 'ready',
    'source_sandbox_id': BOX, 'source_template_id': 'template', 'source_build_id': 'build',
    'vcpu': 2, 'ram_mb': 4096, 'disk_size_mb': 10240, 'architecture': 'amd64',
    'product': 'gmi.sandbox.small', 'metadata': {}, 'created_at': '2026-11-15T10:00:00Z',
    'ready_at': '2026-11-15T10:00:40Z', 'updated_at': '2026-11-15T10:00:40Z',
}


class SnapshotCloud:
    def __init__(self):
        self.requests = []
        self.snapshot_states = ['ready']
        self.create_status = 'creating'
        self.sandbox_states = ['running']
        self.sandbox_extra = {}
        self.snapshots = [dict(SNAPSHOT)]

    def handler(self, request):
        self.requests.append(request)
        assert request.url.host == 'console.gmicloud.ai'
        assert request.headers.get('Authorization') == 'Bearer ' + COMPUTE
        path = request.url.path.removeprefix('/api/v2')
        if path == '/snapshots' and request.method == 'POST':
            assert request.headers['Idempotency-Key']
            body = json.loads(request.content)
            failed = {'failure_code': 'Snapshot.ProviderCreateFailed', 'status_reason': 'disk capture failed',
                      'name': ''} if self.create_status == 'error' else {}
            return httpx.Response(201, json={'request_id': 'r', 'data': {
                **SNAPSHOT, 'name': body.get('name', ''), 'status': self.create_status,
                'source_sandbox_id': body['sandbox_id'], 'ready_at': None, **failed}})
        if path == '/snapshots':
            return httpx.Response(200, json={'request_id': 'r', 'items': self.snapshots,
                                             'total': len(self.snapshots), 'page': 1, 'page_size': 100})
        if path == f'/snapshots/{SNAP}':
            if request.method == 'DELETE':
                return httpx.Response(202, json={'request_id': 'r', 'data': {'snapshot_id': SNAP, 'status': 'deleting'}})
            state = self.snapshot_states.pop(0) if len(self.snapshot_states) > 1 else self.snapshot_states[0]
            extra = {'failure_code': 'Snapshot.ProviderCreateFailed',
                     'status_reason': 'disk capture failed'} if state == 'error' else {}
            return httpx.Response(200, json={'request_id': 'r', 'data': {**SNAPSHOT, 'status': state, **extra}})
        if path == '/products':
            return httpx.Response(200, json={'data': []})
        if path == '/sandboxes' and request.method == 'POST':
            assert request.headers['Idempotency-Key']
            return httpx.Response(201, json={'data': {'id': 'box', 'sandbox_access_token': ACCESS}})
        if path in ('/sandboxes/box/pause', '/sandboxes/box/resume'):
            assert request.method == 'POST'
            state = 'pausing' if path.endswith('pause') else 'resuming'
            return httpx.Response(202, json={'request_id': 'r', 'data': {'id': 'box', 'state': state}})
        if path == '/sandboxes/box':
            state = self.sandbox_states.pop(0) if len(self.sandbox_states) > 1 else self.sandbox_states[0]
            detail = {'id': 'box', 'state': state, 'product': 'gmi.sandbox.small',
                      'idc_name': 'us-central-iowa16', 'started_at': '2026-11-15T10:00:00Z',
                      'end_at': '2026-11-15T10:05:00Z', 'sandbox_access_token': ACCESS, **self.sandbox_extra}
            if state == 'paused':
                detail.update({'paused_at': '2026-11-15T10:01:00Z', 'paused_expire_at': '2026-11-22T10:01:00Z'})
            if state == 'deleting':
                detail['deletion'] = {'code': 'Sandbox.Reclaimed', 'initiator': 'system', 'message': 'Reclaimed'}
            return httpx.Response(200, json={'data': detail})
        raise AssertionError(f'Unexpected request {request.method} {path}')

    def sent(self, method, suffix):
        return [r for r in self.requests if r.method == method and r.url.path.endswith(suffix)]


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path))
    for name in ('GMI_API_KEY', 'GMI_TOKEN', 'GMI_SANDBOX_API_KEY', 'GMI_SANDBOX_IDC_NAME'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('GMI_COMPUTE_API_KEY', COMPUTE)
    fake = SnapshotCloud()
    monkeypatch.setattr(sandbox, 'HTTPTransport', lambda: httpx.MockTransport(fake.handler))
    fake.sleeps = []
    monkeypatch.setattr(sandbox.time, 'sleep', fake.sleeps.append)
    return fake


def invoke(args):
    return CliRunner(mix_stderr=False).invoke(main, ['sandbox', *args])


def output(result):
    text = result.stdout + result.stderr
    assert COMPUTE not in text and ACCESS not in text
    return text


def test_create_from_snapshot_sends_only_snapshot_source(cloud):
    result = invoke(['create', '--snapshot', SNAP, '--yes'])
    assert result.exit_code == 0, output(result)
    body = json.loads(cloud.sent('POST', '/sandboxes')[0].content)
    assert body['snapshot_id'] == SNAP
    assert 'template_id' not in body
    assert result.stdout == 'created sandbox box ends 2026-11-15T10:05:00Z\n'
    output(result)


def test_create_from_template_is_unchanged(cloud):
    result = invoke(['create', 'template', '--yes'])
    assert result.exit_code == 0, output(result)
    body = json.loads(cloud.sent('POST', '/sandboxes')[0].content)
    assert body['template_id'] == 'template'
    assert 'snapshot_id' not in body


@pytest.mark.parametrize('args', [[], ['template', '--snapshot', SNAP]])
def test_create_needs_exactly_one_source(cloud, args):
    result = invoke(['create', *args, '--yes'])
    assert result.exit_code == 1
    assert 'exactly one source' in result.stderr
    assert cloud.requests == []


def test_snapshot_create_waits_at_five_second_intervals(cloud):
    cloud.snapshot_states = ['creating', 'creating', 'ready']
    result = invoke(['snapshots', 'create', BOX, '--name', 'with-jq', '--wait', '--yes'])
    assert result.exit_code == 0, output(result)
    body = json.loads(cloud.sent('POST', '/snapshots')[0].content)
    assert body == {'sandbox_id': BOX, 'name': 'with-jq'}
    assert cloud.sleeps == [5, 5]
    assert result.stdout.splitlines()[-1] == f'ready: snapshot with-jq ({SNAP}) in 0s'
    assert result.stdout.count('capturing') == 2


def test_snapshot_create_without_wait(cloud):
    result = invoke(['snapshots', 'create', BOX, '--yes'])
    assert result.exit_code == 0, output(result)
    assert json.loads(cloud.sent('POST', '/snapshots')[0].content) == {'sandbox_id': BOX}
    assert result.stdout == f'created snapshot {SNAP}, capture started\n'
    assert cloud.sent('GET', SNAP) == []


def test_snapshot_create_reports_immediate_failure(cloud):
    cloud.create_status = 'error'
    result = invoke(['snapshots', 'create', BOX, '--wait', '--yes'])
    assert result.exit_code == 1
    assert 'Snapshot error: disk capture failed (Snapshot.ProviderCreateFailed)' in result.stderr
    assert cloud.sent('GET', SNAP) == []


def test_snapshot_wait_reports_failure_and_stops(cloud):
    cloud.snapshot_states = ['creating', 'error', 'ready']
    result = invoke(['snapshots', 'create', BOX, '--wait', '--yes'])
    assert result.exit_code == 1
    assert 'disk capture failed (Snapshot.ProviderCreateFailed)' in result.stderr
    assert len(cloud.sent('GET', SNAP)) == 2


def test_snapshot_wait_is_bounded(cloud, monkeypatch):
    cloud.snapshot_states = ['creating']
    clock = iter(range(0, 10000, 400))
    monkeypatch.setattr(sandbox.time, 'monotonic', lambda: next(clock))
    result = invoke(['snapshots', 'create', BOX, '--wait', '--timeout', '600', '--yes'])
    assert result.exit_code == 1
    assert 'Timed out waiting for snapshot' in result.stderr


@pytest.mark.parametrize('args', [['snapshots', 'create', BOX], ['snapshots', 'create', BOX, '--json'],
                                  ['snapshots', 'delete', SNAP], ['pause', 'box'], ['resume', 'box']])
def test_writes_refuse_without_confirmation(cloud, args):
    result = invoke(args)
    assert result.exit_code == 1
    assert 'Refusing without confirmation' in result.stderr
    assert cloud.requests == []


def test_snapshot_list_filters_and_table(cloud):
    result = invoke(['snapshots', 'list', '--sandbox', BOX, '--status', 'ready', '--idc-name', 'us-central-iowa16'])
    assert result.exit_code == 0, output(result)
    params = dict(cloud.requests[0].url.params)
    assert params == {'source_sandbox_id': BOX, 'status': 'ready', 'idc_name': 'us-central-iowa16',
                      'page': '1', 'page_size': '100'}
    assert result.stdout == (
        'name\tstatus\tsource\tproduct\tidc\tid\n'
        f'with-jq\tready\t{BOX}\tgmi.sandbox.small\tus-central-iowa16\t{SNAP}\n')


def test_snapshot_list_empty_and_json(cloud):
    cloud.snapshots = []
    assert invoke(['snapshots', 'list']).stdout == 'No snapshots.\n'
    assert invoke(['snapshots', 'list', '--json']).stdout == '[]\n'


def test_snapshot_get_human_and_json(cloud):
    result = invoke(['snapshots', 'get', SNAP])
    assert result.exit_code == 0, output(result)
    assert result.stdout == (
        f'name: with-jq\nid: {SNAP}\nstatus: ready\nsource: {BOX}\nproduct: gmi.sandbox.small\n'
        'vcpu: 2\nmemory_mb: 4096\ndisk_mb: 10240\nidc: us-central-iowa16\n'
        'created: 2026-11-15T10:00:00Z\nready: 2026-11-15T10:00:40Z\n')
    assert json.loads(invoke(['snapshots', 'get', SNAP, '--json']).stdout)['snapshot_id'] == SNAP


def test_snapshot_get_shows_failure(cloud):
    cloud.snapshot_states = ['error']
    result = invoke(['snapshots', 'get', SNAP])
    assert result.exit_code == 0
    assert 'failure code: Snapshot.ProviderCreateFailed\nreason: disk capture failed\n' in result.stdout


@pytest.mark.parametrize('as_json', [False, True])
def test_snapshot_delete(cloud, as_json):
    result = invoke(['snapshots', 'delete', SNAP, '--yes', *(['--json'] if as_json else [])])
    assert result.exit_code == 0, output(result)
    expected = (json.dumps({'snapshot_id': SNAP, 'status': 'deleting'}) + '\n' if as_json
                else f'deleting snapshot {SNAP} (accepted)\n')
    assert result.stdout == expected
    assert len(cloud.requests) == 1


def test_pause_without_wait(cloud):
    result = invoke(['pause', 'box', '--yes'])
    assert result.exit_code == 0, output(result)
    assert result.stdout == 'pausing sandbox box (accepted)\n'
    assert cloud.sent('POST', '/pause')[0].content == b''
    assert cloud.sent('GET', '/sandboxes/box') == []


def test_pause_waits_until_paused(cloud):
    cloud.sandbox_states = ['pausing', 'pausing', 'paused']
    result = invoke(['pause', 'box', '--wait', '--yes'])
    assert result.exit_code == 0, output(result)
    assert result.stdout == 'paused sandbox box reclaimed after 2026-11-22T10:01:00Z\n'
    assert cloud.sleeps == [5, 5]


def test_pause_wait_stops_on_platform_deletion(cloud):
    cloud.sandbox_states = ['deleting']
    result = invoke(['pause', 'box', '--wait', '--yes'])
    assert result.exit_code == 1
    assert 'Sandbox.Reclaimed' in result.stderr and 'not a gmi error' in result.stderr
    assert len(cloud.sent('GET', '/sandboxes/box')) == 1


@pytest.mark.parametrize('args,body', [([], b''), (['--timeout', '600'], b'{"timeout":600}')])
def test_resume_body(cloud, args, body):
    result = invoke(['resume', 'box', *args, '--yes'])
    assert result.exit_code == 0, output(result)
    assert result.stdout == 'resuming sandbox box (accepted)\n'
    assert cloud.sent('POST', '/resume')[0].content.replace(b' ', b'') == body


def test_resume_waits_until_running(cloud):
    cloud.sandbox_states = ['resuming', 'running']
    result = invoke(['resume', 'box', '--wait', '--yes'])
    assert result.exit_code == 0, output(result)
    assert result.stdout == 'resumed sandbox box ends 2026-11-15T10:05:00Z\n'


def test_failed_resume_returns_to_paused(cloud):
    cloud.sandbox_states = ['resuming', 'paused', 'running']
    result = invoke(['resume', 'box', '--wait', '--yes'])
    assert result.exit_code == 1
    assert 'resume did not complete' in result.stderr
    assert len(cloud.sent('GET', '/sandboxes/box')) == 2


def test_resume_json_returns_settled_detail(cloud):
    cloud.sandbox_states = ['running']
    result = invoke(['resume', 'box', '--wait', '--yes', '--json'])
    assert result.exit_code == 0, output(result)
    value = json.loads(result.stdout)
    assert value['state'] == 'running'
    assert value['sandbox_access_token'] == '[redacted]'


def test_get_shows_pause_and_snapshot_fields_only_when_present(cloud):
    cloud.sandbox_states = ['paused']
    cloud.sandbox_extra = {'snapshot_id': SNAP}
    result = invoke(['get', 'box'])
    assert result.exit_code == 0, output(result)
    assert result.stdout.endswith(
        f'ends: 2026-11-15T10:05:00Z\nsnapshot: {SNAP}\npaused: 2026-11-15T10:01:00Z\n'
        'reclaimed after: 2026-11-22T10:01:00Z\n')
    cloud.sandbox_states = ['running']
    cloud.sandbox_extra = {'snapshot_id': None, 'paused_at': None}
    result = invoke(['get', 'box'])
    assert result.stdout.endswith('ends: 2026-11-15T10:05:00Z\n')


def test_agentbox_guide_copies_tested_lifecycle_output():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / 'docs' / 'AGENTBOX.md').read_text()
    for line in ('pausing sandbox box (accepted)', 'paused sandbox box reclaimed after 2026-11-22T10:01:00Z',
                 'resumed sandbox box ends 2026-11-15T10:05:00Z', 'deleting snapshot SNAPSHOT_ID (accepted)',
                 'created snapshot SNAPSHOT_ID, capture started', 'ready: snapshot with-jq (SNAPSHOT_ID) in'):
        assert line in text
