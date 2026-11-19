"""Offline checks for the image publishing contract, not a GitHub execution."""

from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/publish-image.yml'


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def test_image_triggers_permissions_and_ref():
    config = workflow()
    assert config['on'] == {
        'workflow_dispatch': {'inputs': {'ref': {
            'description': 'Version tag to publish, such as v1.0.2',
            'required': True, 'type': 'string',
        }}},
        'push': {'tags': ['v*']},
    }
    assert config['permissions'] == {'contents': 'read'}
    assert len(config['jobs']) == 1
    job = config['jobs']['publish']
    assert job['permissions'] == {'contents': 'read', 'packages': 'write'}
    assert 'inputs.ref' in config['concurrency']['group']
    assert 'github.ref' in config['concurrency']['group']
    assert config['concurrency']['cancel-in-progress'] is False
    checkout = job['steps'][0]
    assert checkout['uses'] == 'actions/checkout@v4'
    assert checkout['with'] == {
        'ref': '${{ inputs.ref || github.ref }}', 'fetch-depth': 0,
    }


def test_image_tags_labels_and_credentials():
    steps = workflow()['jobs']['publish']['steps']
    push = next(step for step in steps if step.get('with', {}).get('push') is True)
    assert push['uses'] == 'docker/build-push-action@v6'
    assert push['with']['context'] == '.'
    assert push['with']['tags'].splitlines() == [
        'ghcr.io/zkasuran/gmi:${{ steps.version.outputs.tag }}',
        'ghcr.io/zkasuran/gmi:${{ steps.version.outputs.version }}',
    ]
    labels = dict(line.split('=', 1) for line in push['with']['labels'].splitlines())
    assert labels == {
        'org.opencontainers.image.source': 'https://github.com/zkasuran/gmi',
        'org.opencontainers.image.description':
            'Community CLI and HTTP chat service for GMI Cloud; not an official GMI product.',
        'org.opencontainers.image.licenses': 'Apache-2.0',
        'org.opencontainers.image.version': '${{ steps.version.outputs.version }}',
    }
    derive = next(step for step in steps if step.get('id') == 'version')['run']
    assert '${tag#v}' in derive
    assert 'refs/tags/$tag^{commit}' in derive
    assert 'Select a version tag' in derive
    text = WORKFLOW.read_text()
    assert ':latest' not in text
    assert re.findall(r'secrets\s*\.\s*(\w+)', text) == ['GITHUB_TOKEN']
    assert text.count('secrets') == 1
    login = next(step for step in steps if step.get('uses') == 'docker/login-action@v3')
    assert login['with'] == {
        'registry': 'ghcr.io', 'username': '${{ github.actor }}',
        'password': '${{ secrets.GITHUB_TOKEN }}',
    }
    assert any(step.get('uses') == 'docker/setup-buildx-action@v3' for step in steps)


def test_image_smoke_test_precedes_push():
    steps = workflow()['jobs']['publish']['steps']
    build_index = next(i for i, step in enumerate(steps)
                       if step.get('with', {}).get('load') is True)
    smoke_index = next(i for i, step in enumerate(steps)
                       if step['name'] == 'Smoke test service')
    push_index = next(i for i, step in enumerate(steps)
                      if step.get('with', {}).get('push') is True)
    assert build_index < smoke_index < push_index
    build = steps[build_index]
    assert build['uses'] == 'docker/build-push-action@v6'
    assert build['with'] == {
        'context': '.', 'load': True, 'push': False, 'tags': 'gmi-smoke',
    }
    smoke = steps[smoke_index]
    assert 'if' not in smoke
    assert not smoke.get('continue-on-error')
    assert not steps[push_index].get('continue-on-error')
    assert 'if' not in steps[push_index]
    script = smoke['run']
    for required in (
        'docker run --detach', '8080:8080', '-e GMI_SERVE_TOKEN=ci-token',
        'SECONDS + 30', '--max-time 1', '/health', '{"status": "ok"}',
        '-X POST', '/chat', 'test "$status" = 401', 'docker logs gmi-smoke',
        "grep -q 'Bearer'", 'docker stop gmi-smoke', 'trap ', ' EXIT',
    ):
        assert required in script
    assert 'API_KEY' not in script
    assert 'set -x' not in script


def test_service_dockerfile_contract():
    dockerfile = (ROOT / 'Dockerfile').read_text()
    assert re.search(r'^USER\s+(?!root\b|0\b)\S+', dockerfile, re.MULTILINE)
    assert re.search(r'^EXPOSE 8080$', dockerfile, re.MULTILINE)
    assert re.search(r'^HEALTHCHECK\s', dockerfile, re.MULTILINE)
    assert 'CMD ["gmi", "serve"]' in dockerfile
