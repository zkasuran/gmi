"""Terminal-independent command, conversation, and input checks."""

import os
import stat
from io import StringIO
from types import SimpleNamespace

from prompt_toolkit.document import Document
import pytest
from rich.console import Console

from gmi.app.commands import Registry, SlashCompleter, parse_command, registry
from gmi.app.state import Conversation, Usage, session_path
from gmi.config import BUILTIN_DEFAULT_MODEL, GMIError
from gmi.app.picker import rank_models


@pytest.fixture
def model_records():
    ids = [
        'nvidia/NVIDIA-Nemotron-3-Super', 'google/gemini-3.8-flash',
        'zai-org/GLM-5.3-Flash', 'openai/gpt-6.1-sol', 'openai/gpt-6.1-tiny',
        'openai/gpt-6', 'openai/gpt-6.1-sol-preview', 'openai/gpt-oss-120b',
        'meta-llama/Llama-4-Maverick', 'meta-llama/Llama-4-Scout',
        'deepseek-ai/DeepSeek-V3.2', 'deepseek-ai/DeepSeek-R1',
        'Qwen/Qwen3-235B', 'Qwen/Qwen3-32B', 'Qwen/Qwen3-Coder',
        'mistralai/Mistral-Small', 'mistralai/Devstral', 'moonshotai/Kimi-K2',
        'zai-org/GLM-4.7', 'google/gemma-3-27b', 'zai-org/GLM-6.1',
        'Qwen/Qwen-6.1', 'mistralai/Mistral-Large',
    ]
    return [{'id': identifier} for identifier in ids]


def test_model_picker_substrings_and_ranking(model_records):
    # Prefix matches lead other substring matches, independent of API order.
    assert rank_models(model_records, '6.1', BUILTIN_DEFAULT_MODEL)[0]['id'] == BUILTIN_DEFAULT_MODEL
    assert all('6.1' in r['id'] for r in rank_models(model_records, '6.1', BUILTIN_DEFAULT_MODEL))
    with_prefix = model_records + [{'id': 'test/6.1-preview'}]
    assert rank_models(with_prefix, '6.1', BUILTIN_DEFAULT_MODEL)[0]['id'] == 'test/6.1-preview'
    assert rank_models(model_records, 'OPENAI/GPT-6.1-SOL', '')[0]['id'] == BUILTIN_DEFAULT_MODEL
    assert rank_models(model_records, '6.1 OPENAI sol', '') == [
        {'id': BUILTIN_DEFAULT_MODEL}, {'id': 'openai/gpt-6.1-sol-preview'}]
    assert rank_models(model_records, 'gpt-6.1-sol', '')[0]['id'] == BUILTIN_DEFAULT_MODEL


def test_model_picker_empty_filter(model_records):
    ordered = rank_models(model_records, '', BUILTIN_DEFAULT_MODEL)
    assert ordered[0]['id'] == BUILTIN_DEFAULT_MODEL
    assert [r['id'] for r in ordered[1:]] == sorted(
        (r['id'] for r in model_records if r['id'] != BUILTIN_DEFAULT_MODEL), key=str.casefold)
    assert rank_models(model_records, '  ', BUILTIN_DEFAULT_MODEL) == ordered


def test_model_picker_no_match(model_records):
    assert rank_models(model_records, 'no-such-model', BUILTIN_DEFAULT_MODEL) == []


def test_registry_parsing_and_suggestions():
    assert parse_command(' /SYSTEM  be brief ') == ('system', 'be brief')
    commands = Registry()
    received = []
    commands.command('help', 'Help')(lambda app, arg: received.append(arg))
    commands.dispatch(None, '/help details')
    assert received == ['details']
    with pytest.raises(ValueError, match='Duplicate'):
        commands.command('help', 'Again')(lambda app, arg: None)
    with pytest.raises(GMIError, match='Did you mean /help'):
        commands.dispatch(None, '/hepl')
    with pytest.raises(GMIError, match='Use /help'):
        commands.dispatch(None, '/zzzzzzzz')


def test_completion_on_slash():
    completions = list(SlashCompleter(registry).get_completions(Document('/'), None))
    assert {c.text for c in completions} == {'/' + name for name in registry.commands}
    assert all(c.display_meta_text for c in completions)
    assert [c.text for c in SlashCompleter(registry).get_completions(Document('/mod'), None)] == ['/model', '/models']


def test_save_load_and_model_switch(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    state = Conversation(system='Be brief', messages=[{'role': 'user', 'content': 'hello'}])
    app = SimpleNamespace(state=state, say=lambda text: None)
    registry.dispatch(app, '/model ' + BUILTIN_DEFAULT_MODEL)
    assert state.messages == [{'role': 'user', 'content': 'hello'}]
    assert state.model == BUILTIN_DEFAULT_MODEL
    name = state.save('round-trip')
    if os.name != 'nt':  # Windows has no POSIX permission bits.
        assert stat.S_IMODE(session_path(name).stat().st_mode) == 0o600
    restored = Conversation()
    restored.load(name)
    assert restored == state
    assert state.save()
    with pytest.raises(GMIError):
        state.save('../escape')
    session_path('broken').write_text('{}')
    with pytest.raises(GMIError, match='Cannot load'):
        state.load('broken')
    assert state.messages == restored.messages


def test_model_rejects_unknown_id_and_suggests_close_match():
    output = StringIO()
    console = Console(file=output, force_terminal=False)
    state = Conversation(model=BUILTIN_DEFAULT_MODEL)
    app = SimpleNamespace(state=state, console=console,
                          say=lambda text: console.print(text, markup=False),
                          model_records=lambda: [{'id': BUILTIN_DEFAULT_MODEL}])
    registry.dispatch(app, '/model openai/gpt-6.1-solx')
    assert state.model == BUILTIN_DEFAULT_MODEL
    assert 'Unknown model openai/gpt-6.1-solx.' in output.getvalue()
    assert 'Did you mean openai/gpt-6.1-sol?' in output.getvalue()


def test_model_accepts_id_when_models_cannot_be_fetched():
    output = StringIO()
    console = Console(file=output, force_terminal=False)
    state = Conversation(model=BUILTIN_DEFAULT_MODEL)
    app = SimpleNamespace(state=state, console=console,
                          say=lambda text: console.print(text, markup=False),
                          model_records=lambda: (_ for _ in ()).throw(GMIError('network')))
    registry.dispatch(app, '/model custom/provider-model')
    assert state.model == 'custom/provider-model'
    assert 'could not verify this model id' in output.getvalue()


def test_usage_only_counts_api_numbers():
    usage = Usage()
    usage.add({'prompt_tokens': 12, 'completion_tokens': 2})
    usage.add(None)
    usage.add({'prompt_tokens': 5})
    assert (usage.input, usage.output, usage.unknown_replies) == (17, 2, 2)


def test_system_clear_preserves_session_usage():
    state = Conversation(messages=[{'role': 'user', 'content': 'hello'}])
    app = SimpleNamespace(state=state, say=lambda text: None)
    registry.dispatch(app, '/system Be brief')
    assert state.system == 'Be brief'
    registry.dispatch(app, '/system clear')
    assert not state.system
    state.usage.add({'prompt_tokens': 1, 'completion_tokens': 2})
    registry.dispatch(app, '/clear')
    assert state.messages == []
    assert state.usage.output == 2
