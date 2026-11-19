"""Friendly AgentBox commands. Data plane contracts are unverified live."""
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time
from urllib.parse import quote
from uuid import uuid4

import click
import httpx

from . import __version__
from .api_commands import _credential, COMPUTE_HINT
from .config import GMIError, read_config

BASE_URL = 'https://console.gmicloud.ai/api/v2'
HTTPTransport = httpx.HTTPTransport


def component(value):
    return quote(str(value), safe='')


def idc_selection(flag=None):
    name = flag or os.environ.get('GMI_SANDBOX_IDC_NAME') or read_config().get('sandbox_idc')
    return {'idc_name': name} if name else {}


class SandboxHTTPError(GMIError):
    def __init__(self, status):
        self.status = status
        super().__init__(f'Sandbox HTTP {status}; check the request and account access.')


class SandboxClient:
    def __init__(self, key=None):
        self.key, _, _ = _credential('sandbox', key, allow_missing=True)
        if not self.key:
            raise GMIError('Missing compute API key.' + COMPUTE_HINT)
        self.secrets = {self.key}
        self.http = httpx.Client(transport=HTTPTransport(), timeout=30,
                                 headers={'User-Agent': f'gmi-cli/{__version__}'})

    def close(self):
        self.http.close()

    def scrub(self, value, show_token=False):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if show_token and key in ('sandbox_access_token', 'traffic_access_token') and isinstance(item, str):
                    # Reveal only these top-level fields, never echoed tokens elsewhere.
                    result[key] = item.replace(self.key, '[redacted]')
                elif 'token' in key.lower() or 'api_key' in key.lower():
                    result[key] = '[redacted]'
                else:
                    result[key] = self.scrub(item)
            return result
        if isinstance(value, list):
            return [self.scrub(item) for item in value]
        if isinstance(value, str):
            for secret in sorted(self.secrets, key=len, reverse=True):
                if secret:
                    value = value.replace(secret, '[redacted]')
        return value

    def _remember(self, value):
        if isinstance(value, dict):
            for k, v in value.items():
                if ('token' in k.lower() or 'api_key' in k.lower()) and isinstance(v, str):
                    self.secrets.add(v)
                self._remember(v)
        elif isinstance(value, list):
            for v in value:
                self._remember(v)

    def request(self, method, path, *, connection=None, binary=False, max_bytes=None, **kwargs):
        headers = kwargs.pop('headers', {})
        if connection:
            base = f"https://{connection['sandbox_key']}.{connection['domain']}"
            headers['X-Access-Token'] = connection['sandbox_access_token']
        else:
            base = BASE_URL
            headers['Authorization'] = 'Bearer ' + self.key
        try:
            if binary and max_bytes is not None:
                with self.http.stream(method, base + path, headers=headers, **kwargs) as response:
                    if response.status_code >= 400:
                        raise SandboxHTTPError(response.status_code)
                    content = bytearray()
                    for chunk in response.iter_bytes(chunk_size=16384):
                        content.extend(chunk)
                        if len(content) > max_bytes:
                            raise GMIError('File exceeds the 256 KB tool limit; use gmi sandbox get-file.')
                    return bytes(content)
            response = self.http.request(method, base + path, headers=headers, **kwargs)
        except httpx.RequestError:
            raise GMIError('Sandbox network request failed; check connectivity and retry.') from None
        if response.status_code >= 400:
            if response.status_code in (401, 403) and not connection:
                raise GMIError(f'Sandbox access rejected ({response.status_code}).' + COMPUTE_HINT)
            # Error envelopes can contain credentials. Never echo a raw body.
            raise SandboxHTTPError(response.status_code)
        if binary:
            return response.content
        try:
            value = response.json() if response.content else {}
        except ValueError:
            raise GMIError('Invalid sandbox response; expected JSON.') from None
        self._remember(value)
        return value

    def data(self, method, path, **kwargs):
        value = self.request(method, path, **kwargs)
        return value.get('data', value) if isinstance(value, dict) else value

    def pages(self, path, params=None):
        rows = []
        page = 1
        while True:
            value = self.request('GET', path, params={**(params or {}), 'page': page, 'page_size': 100})
            items = value.get('items', value.get('data', []))
            if isinstance(items, dict):
                items = items.get('items', [])
            rows.extend(items)
            if len(items) < 100 or len(rows) >= value.get('total', float('inf')):
                return rows
            page += 1

    def check_deletion(self, value, allow_user=False):
        deletion = value.get('deletion') or {}
        if value.get('state') in ('deleting', 'deleted') or deletion.get('requested_at') or deletion.get('code'):
            user_deleted = deletion.get('initiator') == 'user'
            if user_deleted and allow_user:
                return
            reason = f"{deletion.get('message', 'Sandbox was deleted')} (code: {deletion.get('code', 'unknown')}; initiator: {deletion.get('initiator', 'unknown')})"
            suffix = '.' if user_deleted else '. This is a platform deletion, not a gmi error.'
            raise GMIError(self.scrub(reason) + suffix)

    def wait_sandbox(self, sandbox_id, timeout=600, target='running'):
        deadline = time.monotonic() + timeout
        while True:
            value = self.data('GET', f'/sandboxes/{component(sandbox_id)}')
            self.check_deletion(value)
            state = value.get('state')
            if state == target:
                return value
            if state == 'failed':
                failure = value.get('failure') or value.get('latest_operation') or {}
                raise GMIError(self.scrub('Sandbox failed: ' + str(failure.get('message', 'check gmi sandbox get'))))
            if target == 'running' and state == 'paused':
                # A failed resume returns the sandbox to paused with its files intact.
                raise GMIError(f'Sandbox {sandbox_id} is paused; resume did not complete. Retry gmi sandbox resume.')
            if time.monotonic() >= deadline:
                raise GMIError('Timed out waiting for sandbox; inspect it with gmi sandbox get.')
            time.sleep(min(5, max(0, deadline - time.monotonic())))

    def lifecycle(self, sandbox_id, action, timeout=None):
        """Submit pause or resume. The accepted state is not the final outcome."""
        body = {'timeout': timeout} if timeout else None
        return self.data('POST', f'/sandboxes/{component(sandbox_id)}/{action}',
                         **({'json': body} if body else {}))

    def create_snapshot(self, sandbox_id, name=None):
        body = {'sandbox_id': sandbox_id}
        if name:
            body['name'] = name
        return self.data('POST', '/snapshots', headers={'Idempotency-Key': str(uuid4())}, json=body)

    def check_snapshot(self, value):
        """Raise for snapshots that can no longer become ready."""
        status = value.get('status')
        if status in ('error', 'deleting', 'delete_failed'):
            reason = value.get('status_reason') or 'no reason given'
            raise GMIError(self.scrub(f"Snapshot {status}: {reason} ({value.get('failure_code', 'unknown')})"))

    def wait_snapshot(self, snapshot_id, timeout=600, progress=None):
        deadline = time.monotonic() + timeout
        while True:
            value = self.data('GET', f'/snapshots/{component(snapshot_id)}')
            if value.get('status') == 'ready':
                return value
            self.check_snapshot(value)
            if time.monotonic() >= deadline:
                raise GMIError('Timed out waiting for snapshot; inspect it with gmi sandbox snapshots get.')
            if progress:
                progress()
            time.sleep(min(5, max(0, deadline - time.monotonic())))

    def wait_template(self, template_id, timeout=600, progress=None):
        deadline = time.monotonic() + timeout
        while True:
            value = self.data('GET', f'/templates/{component(template_id)}')
            state = value.get('latest_build_status')
            if state == 'ready':
                return value
            if state == 'error':
                build = self.data('GET', f"/templates/{component(template_id)}/builds/{component(value['latest_build_id'])}")
                failure = build.get('failure') or {}
                raise GMIError(self.scrub(f"Template build failed: {failure.get('message', 'unknown failure')} ({failure.get('code', 'unknown')})"))
            if time.monotonic() >= deadline:
                raise GMIError('Timed out waiting for template build; inspect gmi sandbox templates logs.')
            if progress:
                progress()
            time.sleep(min(5, max(0, deadline - time.monotonic())))

    def create_template(self, name, image, product, commands=(), start_cmd=None, idc_name=None):
        build = {'source': {'type': 'image', 'image': image}, 'commands': list(commands)}
        if start_cmd:
            build['start_cmd'] = start_cmd
        return self.data('POST', '/templates', headers={'Idempotency-Key': str(uuid4())}, json={
            'name': name, 'description': 'Created by gmi', **idc_selection(idc_name),
            'resources': {'type': 'preset', 'product': product}, 'build': build})

    def create_sandbox(self, template, timeout=300, env_vars=None, idc_name=None, snapshot=None):
        # The API accepts exactly one source: a template or a ready snapshot.
        source = {'snapshot_id': snapshot} if snapshot else {'template_id': template}
        return self.data('POST', '/sandboxes', headers={'Idempotency-Key': str(uuid4())}, json={
            **source, 'timeout': timeout, 'env_vars': env_vars or {},
            'metadata': {}, **idc_selection(idc_name)})

    def connect(self, sandbox_id):
        return self.data('POST', f'/sandboxes/{component(sandbox_id)}/connect')

    def execute(self, sandbox_id, command, timeout=600):
        connection = self.connect(sandbox_id)
        value = self.data('POST', '/executions', connection=connection,
                          params={'wait': 'true', 'wait_timeout_seconds': 25},
                          json={'action': 'exec', 'parameters': {'command': shlex.join(command)}})
        deadline = time.monotonic() + timeout
        while value.get('status') in ('pending', 'running', 'canceling'):
            if time.monotonic() >= deadline:
                raise GMIError('Timed out waiting for execution; it may still be running.')
            time.sleep(min(1, max(0, deadline - time.monotonic())))
            value = self.data('GET', f"/executions/{component(value['execution_id'])}", connection=connection)
        if value.get('exit_code') is None:
            raise GMIError('Execution ended without a process exit code; inspect the sandbox.')
        return value

    def run_once(self, image, command, product="gmi.sandbox.small", idc_name=None, timeout=300, wait_timeout=None):
        """Run one command and always attempt sandbox cleanup. Keep reusable templates."""
        wait_options = {} if wait_timeout is None else {"timeout": wait_timeout}
        name = 'gmi-run-' + hashlib.sha256((image + '\0' + product).encode()).hexdigest()[:16]
        template_id = None
        for row in self.pages('/templates', idc_selection(idc_name)):
            if row.get('name') == name and row.get('latest_build_status') == 'ready':
                build = self.data('GET', f"/templates/{component(row['id'])}/builds/{component(row['latest_build_id'])}")
                source = build.get('source') or {}
                if source.get('image') == image and build.get('product', row.get('product')) == product:
                    template_id = row['id']
                    break
        if template_id is None:
            template = self.create_template(name, image, product, idc_name=idc_name)
            template_id = template['id']
            self.wait_template(template_id, **wait_options)
        sandbox_id = None
        try:
            box = self.create_sandbox(template_id, timeout, idc_name=idc_name)
            sandbox_id = box['id']
            self.wait_sandbox(sandbox_id, **wait_options)
            result = self.execute(sandbox_id, command, **wait_options)
        finally:
            if sandbox_id:
                pending_error = sys.exc_info()[0] is not None
                try:
                    self.data('DELETE', f'/sandboxes/{component(sandbox_id)}')
                except GMIError as error:
                    click.echo(self.scrub(f'Cleanup failed for sandbox {sandbox_id}: {error}. Run gmi sandbox kill {sandbox_id} --yes.'), err=True)
                    # Do not replace an execution error or Ctrl+C with a cleanup error.
                    if not pending_error:
                        raise
        return result

    def estimate(self, idc_name=None, product=None):
        try:
            products = self.data('GET', '/products', params=idc_selection(idc_name))
        except SandboxHTTPError as error:
            # The catalog requires an explicit IDC in some deployments. Creation
            # can still use the organization's default when IDC is omitted.
            if error.status != 400 or idc_selection(idc_name):
                raise
            products = []
        priced = [p for p in products if (not product or p.get('product') == product) and any(k in p for k in ('price', 'price_info', 'pricing'))]
        if priced:
            text = 'Catalog billing estimate: ' + json.dumps([{k: v for k, v in p.items() if k in ('product', 'price', 'price_info', 'pricing', 'unit', 'currency')} for p in priced])
        else:
            text = 'Billing is per second of runtime and covered by account credits where applicable; catalog prices are unavailable.'
        click.echo(self.scrub(text), err=True)


def command_options(function):
    @click.option('--api-key', help='Compute key override. Prefer the compute key environment variable.')
    @click.option('--json', 'as_json', is_flag=True)
    @click.pass_context
    @wraps(function)
    def wrapped(ctx, api_key, as_json, **kwargs):
        root = ctx.find_root().obj or {}
        client = None
        try:
            client = SandboxClient(api_key or root.get('api_key'))
            return function(client=client, as_json=as_json or root.get('as_json', False), **kwargs)
        except GMIError as error:
            raise click.ClickException(client.scrub(str(error)) if client else str(error)) from None
        except (KeyError, TypeError, ValueError):
            raise click.ClickException('Invalid sandbox response; check the API contract.') from None
        except OSError:
            raise click.ClickException('File operation failed; check the path and permissions.') from None
        finally:
            if client:
                client.close()
    return wrapped


def emit(client, value, as_json=False, show_token=False):
    click.echo(json.dumps(client.scrub(value, show_token), ensure_ascii=False, indent=None if as_json else 2))


def human_field(client, value):
    """Keep each field on one line and redact known credentials."""
    return ' '.join(str(client.scrub(value)).split()) if value is not None else '-'


def human_table(client, rows, columns, empty):
    if not rows:
        click.echo(empty)
        return
    click.echo('\t'.join(label for label, key in columns))
    for row in rows:
        click.echo('\t'.join(human_field(client, row.get(key)) for label, key in columns))


def human_details(client, fields):
    for label, value in fields:
        click.echo(f'{label}: {human_field(client, value)}')


def confirm(yes, as_json, message):
    if yes:
        return
    if as_json or not sys.stdin.isatty():
        raise GMIError('Refusing without confirmation; use --yes (required without a terminal or with --json).')
    if not click.confirm(message, default=False):
        raise GMIError('Cancelled.')


@click.group('sandbox')
def sandbox_group():
    """Manage AgentBox with a compute-scope key. Data plane is unverified live."""


@sandbox_group.command('products')
@click.option('--idc-name')
@command_options
def products(client, as_json, idc_name):
    value = client.data('GET', '/products', params=idc_selection(idc_name))
    if as_json:
        emit(client, value, True)
        return
    price_keys = [k for k in ('price', 'price_info', 'pricing') if any(k in row for row in value)]
    click.echo('sku\tstatus\tvcpu\tmemory_mb' + ('\tprice' if price_keys else ''))
    for row in value:
        resources = row.get('resources') or {}
        fields = [row.get('product', ''), row.get('status', ''), resources.get('vcpu', ''), resources.get('memory_mb', '')]
        if price_keys:
            fields.append(json.dumps({k: row[k] for k in (*price_keys, 'unit', 'currency') if k in row}))
        click.echo(client.scrub('\t'.join(map(str, fields))))


@sandbox_group.group('templates')
def templates_group():
    """Manage image templates and builds."""


@templates_group.command('list')
@click.option('--idc-name')
@command_options
def templates_list(client, as_json, idc_name):
    value = client.pages('/templates', idc_selection(idc_name))
    if as_json:
        emit(client, value, True)
    else:
        human_table(client, value, [('name', 'name'), ('status', 'latest_build_status'),
                                   ('product', 'product'), ('idc', 'idc_name'), ('id', 'id')],
                    'No templates.')


@templates_group.command('get')
@click.argument('template_id')
@command_options
def templates_get(client, as_json, template_id):
    value = client.data('GET', f'/templates/{component(template_id)}')
    if as_json:
        emit(client, value, True)
    else:
        resources = value.get('resources') or {}
        human_details(client, [('name', value.get('name')), ('id', value.get('id')),
                               ('status', value.get('latest_build_status')), ('product', value.get('product')),
                               ('vcpu', resources.get('vcpu', resources.get('cpu_count'))),
                               ('memory_mb', resources.get('memory_mb')), ('idc', value.get('idc_name')),
                               ('created', value.get('created_at'))])


@templates_group.command('create')
@click.option('--name', required=True)
@click.option('--image', default='ubuntu:24.04', show_default=True)
@click.option('--product', default='gmi.sandbox.small', show_default=True)
@click.option('--cmd', multiple=True)
@click.option('--start-cmd')
@click.option('--idc-name')
@click.option('--wait', is_flag=True)
@click.option('--timeout', type=click.IntRange(min=1), default=600, show_default=True)
@click.option('--yes', is_flag=True)
@command_options
def templates_create(client, as_json, name, image, product, cmd, start_cmd, idc_name, wait, timeout, yes):
    client.estimate(idc_name, product)
    confirm(yes, as_json, 'Create a template and build its image?')
    started = time.monotonic()
    value = client.create_template(name, image, product, cmd, start_cmd, idc_name)
    if wait:
        progress = None if as_json else lambda: click.echo(f'building {int(time.monotonic() - started)}s')
        value = client.wait_template(value['id'], timeout, progress=progress)
    if as_json:
        emit(client, value, True)
    else:
        identity = f"{human_field(client, value.get('name', name))} ({human_field(client, value['id'])})"
        if wait:
            click.echo(f"ready: {identity} product {human_field(client, value.get('product', product))} in {int(time.monotonic() - started)}s")
        else:
            click.echo(f'created: {identity}, build started')


@templates_group.command('delete')
@click.argument('template_id')
@click.option('--yes', is_flag=True)
@command_options
def templates_delete(client, as_json, template_id, yes):
    confirm(yes, as_json, 'Delete this template?')
    value = client.data('DELETE', f'/templates/{component(template_id)}')
    if as_json:
        emit(client, value, True)
    else:
        click.echo(f'deleting template {human_field(client, value.get("id", template_id))} (accepted)')


@templates_group.command('logs')
@click.argument('template_id')
@command_options
def templates_logs(client, as_json, template_id):
    template = client.data('GET', f'/templates/{component(template_id)}')
    path = f"/templates/{component(template_id)}/builds/{component(template['latest_build_id'])}/logs"
    entries, offset = [], 0
    while True:
        value = client.data('GET', path, params={'offset': offset, 'limit': 100})
        entries.extend(value['entries'])
        if not value.get('has_more'):
            break
        next_offset = value['next_offset']
        if next_offset <= offset:
            raise GMIError('Build logs returned an invalid pagination offset.')
        offset = next_offset
    if as_json:
        emit(client, entries, True)
    elif entries:
        for entry in entries:
            click.echo(human_field(client, entry['message']))
    else:
        click.echo('No build logs.')


@sandbox_group.command('create')
@click.argument('template', required=False)
@click.option('--snapshot', help='Create from a ready snapshot instead of a template.')
@click.option('--timeout', type=click.IntRange(min=1), default=300, show_default=True)
@click.option('--env', 'envs', multiple=True)
@click.option('--idc-name')
@click.option('--wait', is_flag=True)
@click.option('--wait-timeout', type=click.IntRange(min=1), default=600)
@click.option('--show-token', is_flag=True, help='Reveal sandbox secrets once. Handle output privately.')
@click.option('--yes', is_flag=True)
@command_options
def sandbox_create(client, as_json, template, snapshot, timeout, envs, idc_name, wait, wait_timeout, show_token, yes):
    """Create a sandbox from TEMPLATE or from --snapshot ID."""
    if bool(template) == bool(snapshot):
        raise GMIError('Give exactly one source: a TEMPLATE argument or --snapshot ID.')
    variables = {}
    for item in envs:
        if '=' not in item or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', item.split('=', 1)[0]):
            raise GMIError('--env requires K=V with a valid environment variable name.')
        key, value = item.split('=', 1)
        variables[key] = value
    client.estimate(idc_name)
    confirm(yes, as_json, 'Create a billable sandbox?')
    value = client.create_sandbox(template, timeout, variables, idc_name, snapshot)
    sandbox_id = value['id']
    try:
        details = client.wait_sandbox(sandbox_id, wait_timeout) if wait else client.data('GET', f'/sandboxes/{component(sandbox_id)}')
    except GMIError:
        click.echo(f'Created sandbox {sandbox_id}; inspect it with gmi sandbox get {sandbox_id}.', err=True)
        raise
    client.check_deletion(details)
    value = {**value, **details, 'id': sandbox_id}
    value.setdefault('end_at', None)
    if show_token and as_json:
        click.echo('Warning: sandbox access and traffic tokens are secrets. Shown once below.', err=True)
    if as_json:
        emit(client, value, True, show_token)
    else:
        click.echo(f'created sandbox {human_field(client, sandbox_id)} ends {human_field(client, value["end_at"])}')


@sandbox_group.command('list')
@click.option('--state')
@click.option('--idc-name')
@command_options
def sandbox_list(client, as_json, state, idc_name):
    params = idc_selection(idc_name)
    if state:
        params['state'] = state
    value = client.pages('/sandboxes', params)
    if as_json:
        emit(client, value, True)
    else:
        human_table(client, value, [('id', 'id'), ('state', 'state'), ('product', 'product'),
                                   ('idc', 'idc_name'), ('ends', 'end_at')], 'No sandboxes.')


@sandbox_group.command('get')
@click.argument('sandbox_id')
@command_options
def sandbox_get(client, as_json, sandbox_id):
    value = client.data('GET', f'/sandboxes/{component(sandbox_id)}')
    if not as_json:
        fields = [('id', value.get('id')), ('state', value.get('state')), ('product', value.get('product')),
                  ('idc', value.get('idc_name')), ('started', value.get('started_at')), ('ends', value.get('end_at'))]
        # Only shown when present so output for template sandboxes is unchanged.
        fields.extend((label, value[key]) for label, key in
                      [('snapshot', 'snapshot_id'), ('paused', 'paused_at'), ('reclaimed after', 'paused_expire_at')]
                      if value.get(key))
        deletion = value.get('deletion') or {}
        if deletion:
            fields.extend((label, deletion.get(key)) for label, key in
                          [('deletion code', 'code'), ('deletion message', 'message'), ('initiator', 'initiator')])
        human_details(client, fields)
    client.check_deletion(value, allow_user=True)
    if as_json:
        emit(client, value, True)


@sandbox_group.command('kill')
@click.argument('sandbox_id')
@click.option('--yes', is_flag=True)
@command_options
def sandbox_kill(client, as_json, sandbox_id, yes):
    confirm(yes, as_json, 'Kill this sandbox?')
    value = client.data('DELETE', f'/sandboxes/{component(sandbox_id)}')
    if as_json:
        emit(client, value, True)
    else:
        click.echo(f'killing sandbox {human_field(client, value.get("id", sandbox_id))} (accepted)')


@sandbox_group.command('timeout')
@click.argument('sandbox_id')
@click.argument('seconds', type=click.IntRange(min=1))
@click.option('--yes', is_flag=True)
@command_options
def sandbox_timeout(client, as_json, sandbox_id, seconds, yes):
    confirm(yes, as_json, 'Change the billable sandbox lifetime?')
    value = client.data('POST', f'/sandboxes/{component(sandbox_id)}/timeout', json={'timeout': seconds})
    if as_json:
        emit(client, value, True)
    else:
        click.echo(f'timeout set: {human_field(client, value["id"])} ends {human_field(client, value["new_end_at"])}')


@sandbox_group.command('pause')
@click.argument('sandbox_id')
@click.option('--wait', is_flag=True, help='Poll until the sandbox reports paused.')
@click.option('--wait-timeout', type=click.IntRange(min=1), default=600, show_default=True)
@click.option('--yes', is_flag=True)
@command_options
def sandbox_pause(client, as_json, sandbox_id, wait, wait_timeout, yes):
    """Pause a running sandbox, keeping its disk, memory and processes."""
    confirm(yes, as_json, 'Pause this sandbox? It is reclaimed if it stays paused past its retention window.')
    value = client.lifecycle(sandbox_id, 'pause')
    if wait:
        value = client.wait_sandbox(sandbox_id, wait_timeout, target='paused')
    if as_json:
        emit(client, value, True)
    elif wait:
        click.echo(f'paused sandbox {human_field(client, sandbox_id)} reclaimed after '
                   f'{human_field(client, value.get("paused_expire_at"))}')
    else:
        click.echo(f'pausing sandbox {human_field(client, value.get("id", sandbox_id))} (accepted)')


@sandbox_group.command('resume')
@click.argument('sandbox_id')
@click.option('--timeout', type=click.IntRange(min=1),
              help='Seconds to run after resuming. Default: the runtime left when it was paused.')
@click.option('--wait', is_flag=True, help='Poll until the sandbox reports running.')
@click.option('--wait-timeout', type=click.IntRange(min=1), default=600, show_default=True)
@click.option('--yes', is_flag=True)
@command_options
def sandbox_resume(client, as_json, sandbox_id, timeout, wait, wait_timeout, yes):
    """Resume a paused sandbox. Billable runtime starts again."""
    confirm(yes, as_json, 'Resume this billable sandbox?')
    value = client.lifecycle(sandbox_id, 'resume', timeout)
    if wait:
        value = client.wait_sandbox(sandbox_id, wait_timeout)
    if as_json:
        emit(client, value, True)
    elif wait:
        click.echo(f'resumed sandbox {human_field(client, sandbox_id)} ends {human_field(client, value.get("end_at"))}')
    else:
        click.echo(f'resuming sandbox {human_field(client, value.get("id", sandbox_id))} (accepted)')


@sandbox_group.group('snapshots')
def snapshots_group():
    """Save a running sandbox's filesystem and start new sandboxes from it."""


def snapshot_identity(client, value, fallback=None):
    name = value.get('name')
    identity = human_field(client, value.get('snapshot_id', fallback))
    return f'{human_field(client, name)} ({identity})' if name else identity


@snapshots_group.command('create')
@click.argument('sandbox_id')
@click.option('--name', help='Unique name within the organization.')
@click.option('--wait', is_flag=True, help='Poll every five seconds until the snapshot is ready.')
@click.option('--timeout', type=click.IntRange(min=1), default=600, show_default=True)
@click.option('--yes', is_flag=True)
@command_options
def snapshots_create(client, as_json, sandbox_id, name, wait, timeout, yes):
    """Snapshot the filesystem of a running sandbox. Processes and memory are not kept."""
    confirm(yes, as_json, 'Create a durable snapshot of this sandbox?')
    started = time.monotonic()
    value = client.create_snapshot(sandbox_id, name)
    # A definitive capture failure still returns 201 with status error.
    client.check_snapshot(value)
    if wait:
        progress = None if as_json else lambda: click.echo(f'capturing {int(time.monotonic() - started)}s')
        value = client.wait_snapshot(value['snapshot_id'], timeout, progress=progress)
    if as_json:
        emit(client, value, True)
    elif wait:
        click.echo(f'ready: snapshot {snapshot_identity(client, value)} in {int(time.monotonic() - started)}s')
    else:
        click.echo(f'created snapshot {snapshot_identity(client, value)}, capture started')


@snapshots_group.command('list')
@click.option('--sandbox', 'source', help='Only snapshots taken from this sandbox.')
@click.option('--status', type=click.Choice(['creating', 'ready', 'error', 'deleting', 'delete_failed']))
@click.option('--idc-name')
@command_options
def snapshots_list(client, as_json, source, status, idc_name):
    params = idc_selection(idc_name)
    if source:
        params['source_sandbox_id'] = source
    if status:
        params['status'] = status
    value = client.pages('/snapshots', params)
    if as_json:
        emit(client, value, True)
    else:
        human_table(client, value, [('name', 'name'), ('status', 'status'), ('source', 'source_sandbox_id'),
                                   ('product', 'product'), ('idc', 'idc_name'), ('id', 'snapshot_id')],
                    'No snapshots.')


@snapshots_group.command('get')
@click.argument('snapshot_id')
@command_options
def snapshots_get(client, as_json, snapshot_id):
    value = client.data('GET', f'/snapshots/{component(snapshot_id)}')
    if as_json:
        emit(client, value, True)
        return
    fields = [('name', value.get('name') or None), ('id', value.get('snapshot_id')), ('status', value.get('status')),
              ('source', value.get('source_sandbox_id')), ('product', value.get('product')),
              ('vcpu', value.get('vcpu')), ('memory_mb', value.get('ram_mb')), ('disk_mb', value.get('disk_size_mb')),
              ('idc', value.get('idc_name')), ('created', value.get('created_at')), ('ready', value.get('ready_at'))]
    fields.extend((label, value[key]) for label, key in
                  [('failure code', 'failure_code'), ('reason', 'status_reason')] if value.get(key))
    human_details(client, fields)


@snapshots_group.command('delete')
@click.argument('snapshot_id')
@click.option('--yes', is_flag=True)
@command_options
def snapshots_delete(client, as_json, snapshot_id, yes):
    confirm(yes, as_json, 'Delete this snapshot?')
    value = client.data('DELETE', f'/snapshots/{component(snapshot_id)}')
    if as_json:
        emit(client, value, True)
    else:
        click.echo(f'deleting snapshot {human_field(client, value.get("snapshot_id", snapshot_id))} (accepted)')


def execution_output(client, value, as_json):
    if as_json:
        emit(client, value, True)
    else:
        click.echo(client.scrub(value.get('stdout') or ''), nl=False)
        click.echo(client.scrub(value.get('stderr') or ''), nl=False, err=True)
    raise click.exceptions.Exit(value['exit_code'])


@sandbox_group.command('exec')
@click.argument('sandbox_id')
@click.argument('command', nargs=-1, required=True, type=click.UNPROCESSED)
@command_options
def sandbox_exec(client, as_json, sandbox_id, command):
    execution_output(client, client.execute(sandbox_id, command), as_json)


@sandbox_group.command('put')
@click.argument('sandbox_id')
@click.argument('local', type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument('remote')
@click.option('--username')
@command_options
def sandbox_put(client, as_json, sandbox_id, local, remote, username):
    connection = client.connect(sandbox_id)
    params = {'path': remote}
    if username:
        params['username'] = username
    with local.open('rb') as handle:
        value = client.data('POST', '/files', connection=connection, params=params,
                            files={'file': (local.name, handle, 'application/octet-stream')})
    if as_json:
        emit(client, value, True)
    else:
        click.echo(f'uploaded {human_field(client, local)} to {human_field(client, remote)}')


@sandbox_group.command('get-file')
@click.argument('sandbox_id')
@click.argument('remote')
@click.option('--out', 'output', type=click.Path(dir_okay=False, path_type=Path))
@click.option('--username')
@command_options
def sandbox_get_file(client, as_json, sandbox_id, remote, output, username):
    destination = output or Path(remote).name
    if not destination:
        raise GMIError('Use --out with a destination file.')
    connection = client.connect(sandbox_id)
    params = {'path': remote}
    if username:
        params['username'] = username
    content = client.request('GET', '/files', connection=connection, params=params, binary=True)
    Path(destination).write_bytes(content)
    if as_json:
        emit(client, {'path': str(destination), 'bytes': len(content)}, True)
    else:
        click.echo(f'downloaded {human_field(client, remote)} to {human_field(client, destination)} ({len(content)} bytes)')


@sandbox_group.command('run')
@click.option('--image', required=True)
@click.option('--product', default='gmi.sandbox.small', show_default=True)
@click.option('--idc-name')
@click.option('--timeout', type=click.IntRange(min=1), default=300)
@click.option('--yes', is_flag=True)
@click.argument('command', nargs=-1, required=True, type=click.UNPROCESSED)
@command_options
def sandbox_run(client, as_json, image, product, idc_name, timeout, yes, command):
    client.estimate(idc_name, product)
    confirm(yes, as_json, 'Build a template and run a billable sandbox, then kill it?')
    result = client.run_once(image, command, product, idc_name, timeout)
    execution_output(client, result, as_json)
