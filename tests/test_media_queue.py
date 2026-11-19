"""Fake-server tests for the media request queue flow; no key and no live calls."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

from click.testing import CliRunner
import pytest

from gmi import media
from gmi.cli import main


UPSTREAM_500 = {'error': 'Upstream provider temporarily unavailable'}
RATE_LIMIT_429 = {'error': 'rate limited', 'model': 'cheap-image',
                  'rate_limit': {'type': 'rate_limit_error', 'limit_type': 'RPD', 'limit': 50, 'retry_after_ms': 60000}}


def record(name, kind='image', price=20000, qworkers=1, unit='image'):
    return {'model': name, 'model_type': kind, 'status': 'active', 'qworkers': qworkers, 'model_metadata': {},
            'modalities': {'input': [{'type': 'text'}], 'output': [{'type': kind}]},
            'parameters': [{'name': 'prompt', 'type': 'string', 'required': True}],
            'price_info': {'price': price, 'unit': unit}}


class FakeQueue:
    """Serves the catalogue and the request queue the way MediaClient expects."""

    def __init__(self):
        self.models = {}
        self.submit_responses = []
        self.requests = []
        self.posts = []
        self.server = None

    def add(self, model):
        self.models[model['model']] = model

    def start(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                fake.requests.append(('GET', self.path, dict(self.headers)))
                if self.path == '/models':
                    return self.reply(200, {'model_ids': list(fake.models)})
                if self.path.startswith('/models/'):
                    name = self.path[len('/models/'):]
                    if name in fake.models:
                        return self.reply(200, {'data': fake.models[name]})
                    return self.reply(404, {'error': 'unknown model'})
                if self.path.startswith('/requests/'):
                    return self.reply(200, {'request_id': self.path.rsplit('/', 1)[1], 'status': 'success',
                                            'outcome': {'media_urls': []}})
                return self.reply(404, {'error': 'unexpected path ' + self.path})

            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(length) or b'{}')
                fake.requests.append(('POST', self.path, dict(self.headers)))
                fake.posts.append(body)
                if self.path != '/requests':
                    return self.reply(404, {'error': 'unexpected path ' + self.path})
                status, reply = fake.submit_responses.pop(0) if fake.submit_responses else (
                    200, {'request_id': 'req-1', 'status': 'dispatched'})
                return self.reply(status, reply)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return 'http://127.0.0.1:%d' % self.server.server_port

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def queue(monkeypatch, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setattr(media, 'cache_path', lambda: tmp_path / 'media-models.json')
    monkeypatch.setenv('GMI_API_KEY', 'fake-media-key')
    fake = FakeQueue()
    fake.add(record('cheap-image'))
    base = fake.start()
    monkeypatch.setenv('GMI_MEDIA_BASE_URL', base)
    monkeypatch.chdir(tmp_path)
    try:
        yield fake
    finally:
        fake.stop()


def invoke(args, **kwargs):
    return CliRunner(mix_stderr=False).invoke(main, args, **kwargs)


def posts(fake):
    return [path for method, path, _ in fake.requests if method == 'POST']


def test_price_is_shown_before_submission_and_key_is_sent(queue):
    result = invoke(['image', 'a red bicycle'])
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    lines = result.stdout.splitlines()
    assert lines[0] == 'Price: $0.0200 per image'
    assert 'Model: cheap-image' in lines and 'Request: req-1' in lines
    assert posts(queue) == ['/requests']
    assert queue.posts == [{'model': 'cheap-image', 'payload': {'prompt': 'a red bicycle'}}]
    assert all(headers['Authorization'] == 'Bearer fake-media-key' for _, _, headers in queue.requests)
    assert 'fake-media-key' not in result.stdout + result.stderr


@pytest.mark.parametrize('price,confirms', [(249999, False), (250000, True)])
def test_confirmation_prompt_starts_at_twenty_five_cents(queue, price, confirms):
    queue.add(record('priced', price=price))
    result = invoke(['image', 'hello', '--model', 'priced'], input='n\n')
    if confirms:
        assert result.exit_code == 1
        assert 'Continue? [y/N]' in result.stdout
        assert 'Cancelled.' in result.stderr
        assert posts(queue) == []
    else:
        assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
        assert 'Continue?' not in result.stdout
        assert posts(queue) == ['/requests']


def test_yes_flag_and_json_skip_the_prompt(queue):
    queue.add(record('priced', price=250000))
    for extra in (['--yes'], ['--json']):
        result = invoke(['image', 'hello', '--model', 'priced', *extra])
        assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
        assert 'Continue?' not in result.stdout
    assert json.loads(result.stdout)['estimated_cost'] == 0.25
    assert posts(queue) == ['/requests', '/requests']


def test_model_with_zero_workers_is_not_offered(queue):
    queue.add(record('dead', price=1, qworkers=0))
    listed = invoke(['media', 'models', '--type', 'image'])
    assert listed.exit_code == 0, (listed.stdout, listed.stderr, listed.exception)
    assert 'cheap-image' in listed.stdout and 'dead' not in listed.stdout
    picked = invoke(['image', 'hello'])
    assert picked.exit_code == 0, (picked.stdout, picked.stderr, picked.exception)
    assert queue.posts[-1]['model'] == 'cheap-image'
    explicit = invoke(['image', 'hello', '--model', 'dead'])
    assert explicit.exit_code == 1
    assert 'No active image model named dead' in explicit.stderr
    assert posts(queue) == ['/requests']


def test_upstream_500_is_retried_once_then_succeeds(queue):
    # MediaClient.run waits three seconds before the retry; the fake server only
    # fails the first submission.
    queue.submit_responses = [(500, UPSTREAM_500)]
    result = invoke(['image', 'hello'])
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    assert posts(queue) == ['/requests', '/requests']
    assert 'Request: req-1' in result.stdout


def test_other_500_is_not_retried(queue):
    queue.submit_responses = [(500, {'error': 'internal error'})]
    result = invoke(['image', 'hello'])
    assert result.exit_code == 1
    assert 'Media API returned HTTP 500: internal error' in result.stderr
    assert posts(queue) == ['/requests']


def test_429_is_not_retried_and_names_the_daily_limit(queue):
    queue.submit_responses = [(429, RATE_LIMIT_429)]
    result = invoke(['image', 'hello'])
    assert result.exit_code == 1
    assert 'Daily limit reached for cheap-image: 50 requests per day' in result.stderr
    assert 'gmi media models --type image' in result.stderr
    assert posts(queue) == ['/requests']
    plain = invoke(['image', 'hello'])
    assert plain.exit_code == 0, 'a later request without the 429 body still goes through'
