"""The voice director's configuration and its apply script, checked without a key or a
network: the file names exactly the client tools the panel handles, ends a session at the
installation's session maximum, requires a server-minted token, records no voice, allows no
burst calls, and holds no secret. A dry run validates and prints its plan, and never reads a
key or opens a connection."""

import importlib.util
import json
import re
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
TOOLS_TS = REPO / 'cockpit/web/src/app/voice-director/tools.ts'
CONFIG_PY = REPO / 'cockpit/server/app/config.py'
ENGINES = REPO / 'configs/engines.json'


def load_apply():
    spec = importlib.util.spec_from_file_location('voice_director_apply', REPO / 'tools/voice-director/apply.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


apply = load_apply()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError('the voice director config tests open no connection')
    monkeypatch.setattr(urllib.request, 'urlopen', refuse)
    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', refuse)


def config() -> dict:
    return apply.load()


def voice_design_rows() -> list[dict]:
    """The rows of configs/engines.json whose voice design takes a preview line."""
    rows = json.loads(ENGINES.read_text(encoding='utf-8'))['engines']
    return [row for row in rows if ((row.get('params') or {}).get('voice-design') or {}).get('preview_text')]


def preview_line_range(row: dict) -> tuple[int, int]:
    """The shortest and longest preview line a row accepts; a length it leaves out is unbounded."""
    line = row['params']['voice-design']['preview_text']
    return line.get('min_length') or 0, line.get('max_length') or 10**9


def test_the_shipped_configuration_is_valid():
    assert apply.problems(config()) == []


def test_the_tools_are_exactly_the_ones_the_panel_handles():
    panel = re.search(r'TOOL_NAMES = \[([^\]]*)\]', TOOLS_TS.read_text(encoding='utf-8')).group(1)
    handled = sorted(re.findall(r"'([a-z_]+)'", panel))
    assert sorted(tool['name'] for tool in config()['client_tools']) == handled == sorted(apply.TOOLS)


def test_a_session_ends_at_the_installations_session_maximum():
    default = re.search(r"CREXIL_VOICE_SESSION_MAX_S['\"],\s*['\"]?(\d+)", CONFIG_PY.read_text(encoding='utf-8'))
    assert default, 'config.py names the session maximum default'
    assert config()['conversation_config']['conversation']['max_duration_seconds'] == int(default.group(1)) == apply.SESSION_MAX_S


def test_only_a_server_minted_token_starts_it_and_no_voice_is_recorded():
    agent = config()
    assert agent['platform_settings']['auth']['enable_auth'] is True
    assert agent['platform_settings']['privacy']['record_voice'] is False
    assert agent['conversation_config']['agent']['prompt']['enable_parallel_tool_calls'] is False


def test_the_providers_call_limits_back_the_installations_minutes_cap():
    # Crexil's minutes cap stays the guard. ElevenLabs' own limits back it: no burst
    # conversations past the concurrency limit at the higher rate, and ten a day.
    limits = {'daily_limit': 10, 'agent_concurrency_limit': 1, 'bursting_enabled': False}
    assert config()['platform_settings']['call_limits'] == limits
    assert apply.agent_body(config(), ['tool'] * len(apply.TOOLS))['platform_settings']['call_limits'] == limits


def test_the_director_waits_out_a_playing_take_before_it_speaks_up():
    # The panel mutes the microphone while a take plays, so the director waits ElevenAgents'
    # longest silence, 30 seconds, before it takes the turn again.
    assert config()['conversation_config']['turn']['turn_timeout'] == 30
    assert apply.agent_body(config(), ['tool'] * len(apply.TOOLS))['conversation_config']['turn']['turn_timeout'] == 30


def test_the_director_states_a_prepared_run_as_prepare_run_returns_it():
    # What a run reserves is prepare_run's to state, for the whole run and as a reservation (the
    # charge can come in lower), or that the run is free; never per attempt and never worked out
    # by the director.
    agent = config()
    prompt = agent['conversation_config']['agent']['prompt']['prompt']
    assert ('Before preparing a run, say which of those engines and how many takes, and the cap you will set, '
            'but no price until prepare_run returns one.') in prompt
    assert ("When prepare_run returns, say the run's attempts and what the whole run reserves, its reserved_usd exactly "
            'as returned, so it costs up to that amount, or that the run is free when its cost says so; never a price '
            'per attempt, per take or per second, and never one you worked out.') in prompt
    assert 'as the price of the whole run' not in prompt
    prepare = next(tool for tool in agent['client_tools'] if tool['name'] == 'prepare_run')
    assert prepare['description'].endswith("It returns the run's attempts and reserved_usd, the whole run's reservation, "
                                           'or its cost when the run is free: say those as returned.')


def test_the_director_lists_the_engines_before_it_names_one():
    # The director speaks before it uses a tool, so the engines it names come from list_engines,
    # never from a guess: it calls list_engines when the artist asks for a run, names or describes
    # only an engine it returned, and says engines, prices and take labels only as its tools state
    # them. The panel refuses an id list_engines never returned; these words keep a guess out of
    # what the artist hears.
    agent = config()
    prompt = agent['conversation_config']['agent']['prompt']['prompt']
    assert ('When the artist asks for a run, call list_engines first, before you name any engine. Never name or '
            'describe an engine, local or hosted, free or paid, that list_engines has not returned, and use only the '
            'ids it returned.') in prompt
    assert prompt.index('call list_engines first') < prompt.index('Before preparing a run')
    assert 'Say engines, prices and take labels only as your tools state them.' in prompt
    assert 'Say prices only as your tools state them.' not in prompt
    tools = {tool['name']: tool for tool in agent['client_tools']}
    assert tools['list_engines']['description'].endswith('Read-only. Call it before you name or choose an engine.')
    assert 'Call list_engines first; then say the engines it returned' in tools['prepare_run']['description']


def test_the_director_writes_the_preview_line_unless_the_artist_gives_one_and_prepares_in_the_same_turn():
    # Every voice a design attempt makes speaks the whole preview line, and ElevenLabs bills the
    # line by its characters, so unless the artist gives one, the director writes it at about 120
    # to 150 characters and keeps it for the next attempt, so the voices compare. It prepares in
    # the same turn it states the plan: the run card's cap and priced Run, never a spoken yes, are
    # where the artist approves a paid run.
    agent = config()
    prompt = agent['conversation_config']['agent']['prompt']['prompt']
    assert 'Then prepare it in the same turn.' in prompt
    assert ("For voice design, unless the artist gives a preview line, write one yourself in the character's voice, "
            'about 120 to 150 characters, 25 to 30 words: the engine accepts 100 to 1000, and each character costs. '
            'Never ask the artist to approve the line. Keep the same line for the next attempt after a note, so the '
            'voices compare.') in prompt
    assert 'Voice design needs a preview line' not in prompt
    prepare = next(tool for tool in agent['client_tools'] if tool['name'] == 'prepare_run')
    assert ('Call list_engines first; then say the engines it returned, the takes and the cap to the artist, but no '
            'price, and call this in the same turn without asking for approval.') in prepare['description']
    assert prepare['parameters']['properties']['preview_line']['description'] == (
        "voice-design only: the words every preview speaks. Unless the artist gives one, write it yourself in the "
        "character's voice, about 120 to 150 characters, 25 to 30 words (100 to 1000 accepted, and each character "
        'costs). Keep the same line for the next attempt after a note, so the voices compare.')


def test_the_line_the_director_aims_for_is_one_every_voice_design_engine_accepts():
    # The director's numbers follow configs/engines.json: the prompt and preview_line state the
    # range every voice design engine accepts, and the length they aim for sits inside it, above
    # the shortest line accepted, so a line that comes out a little short still passes review.
    agent = config()
    prepare = next(tool for tool in agent['client_tools'] if tool['name'] == 'prepare_run')
    ranges = [preview_line_range(row) for row in voice_design_rows()]
    assert ranges, 'configs/engines.json lists a voice design engine that takes a preview line'
    accepted = (max(low for low, _ in ranges), min(high for _, high in ranges))
    for text in (agent['conversation_config']['agent']['prompt']['prompt'],
                 prepare['parameters']['properties']['preview_line']['description']):
        stated = re.search(r'accepts (\d+) to (\d+)|(\d+) to (\d+) accepted', text)
        aim = re.search(r'about (\d+) to (\d+) characters', text)
        assert stated and aim, text
        assert tuple(int(number) for number in stated.groups() if number) == accepted, text
        shortest, longest = map(int, aim.groups())
        assert accepted[0] < shortest < longest <= accepted[1], text


def test_with_no_cap_named_the_director_sets_ten_cents_for_each_attempt_and_keeps_a_named_cap():
    # The director never guesses a price before prepare_run answers, so a cap the artist did not
    # name is a fixed ten cents for each attempt, which covers one attempt of the longest line any
    # voice design engine accepts, by its tariff. A cap the artist named stays theirs: a run that
    # needs more waits for the artist to name another.
    agent = config()
    prompt = agent['conversation_config']['agent']['prompt']['prompt']
    assert ('If the artist names no cap, set a ten-cent cap for each attempt. Keep a cap the artist named; if the run '
            'needs more, say so and let them name another. When the artist asks for a run,') in prompt
    assert 'smallest round' not in prompt
    prepare = next(tool for tool in agent['client_tools'] if tool['name'] == 'prepare_run')
    assert prepare['parameters']['properties']['spending_limit_usd']['description'] == (
        "The run's cap in US dollars: the artist's, or 0.1 for each attempt when they name none. Keep a cap the artist "
        'named. Tell the artist this number before preparing.')
    for row in voice_design_rows():
        tariff = (row.get('tariffs') or {}).get('voice-design') or {}
        assert tariff.get('unit') in ('1k_characters', 'attempt'), f"{row['id']}: check that ten cents covers its attempt"
        per_attempt = tariff['micro_usd'] * (preview_line_range(row)[1] / 1000 if tariff['unit'] == '1k_characters' else 1)
        assert per_attempt <= 100_000, row['id']


def test_the_file_holds_no_secret_and_no_account_id():
    text = (REPO / 'tools/voice-director/agent.json').read_text(encoding='utf-8')
    assert 'xi-api-key' not in text and 'agent_id' not in text and 'tool_ids' not in text
    assert not re.search(r'\bsk_[A-Za-z0-9]{8,}', text)


def test_every_tool_waits_for_its_answer_and_describes_every_parameter():
    for tool in config()['client_tools']:
        assert tool['type'] == 'client' and tool['expects_response'] is True, tool['name']
        properties = tool['parameters']['properties']
        assert set(tool['parameters']['required']) <= set(properties), tool['name']
        assert all(spec.get('description') for spec in properties.values()), tool['name']


@pytest.mark.parametrize('change, words', [
    (lambda c: c['client_tools'].pop(), 'The client tools must be exactly'),
    (lambda c: c['conversation_config']['conversation'].update(max_duration_seconds=900), 'max_duration_seconds is 900'),
    (lambda c: c['platform_settings']['auth'].update(enable_auth=False), 'must require authentication'),
    (lambda c: c['platform_settings']['privacy'].update(record_voice=True), 'Voice recording must be off'),
    (lambda c: c['conversation_config']['agent']['prompt'].update(enable_parallel_tool_calls=True), 'Parallel tool calls must be off'),
    (lambda c: c['conversation_config']['agent']['prompt'].update(tool_ids=['t1']), 'tool_ids belong to an installation'),
    (lambda c: c.update(note='sk_live0123456789abcdef'), 'no key and no account id'),
    # ElevenLabs allows burst calls by default, so a file that does not refuse them allows them.
    (lambda c: c['platform_settings']['call_limits'].update(bursting_enabled=True), 'Burst calls must be off'),
    (lambda c: c['platform_settings']['call_limits'].pop('bursting_enabled'), 'Burst calls must be off'),
    (lambda c: c['platform_settings'].pop('call_limits'), 'Burst calls must be off'),
])
def test_a_configuration_that_breaks_a_rule_is_refused(change, words):
    broken = config()
    change(broken)
    assert any(words in problem for problem in apply.problems(broken))


def test_a_dry_run_validates_and_prints_its_plan_without_a_key_or_a_connection(monkeypatch, capsys):
    monkeypatch.setenv('ELEVENLABS_API_KEY', 'sk_never0read0by0a0dry0run')
    assert apply.main(['--dry-run']) == 0
    printed = capsys.readouterr().out
    plan = json.loads(printed)
    assert plan['valid'] is True
    assert 'sk_never0read0by0a0dry0run' not in printed and 'xi-api-key' not in printed
    steps = plan['plan']
    assert steps[0]['path'] == '/v1/convai/llm/list'
    assert [step['body']['tool_config']['name'] for step in steps[1:-1]] == [tool['name'] for tool in config()['client_tools']]
    agent = steps[-1]
    assert (agent['method'], agent['path']) == ('POST', '/v1/convai/agents/create')
    assert 'client_tools' not in agent['body']
    assert len(agent['body']['conversation_config']['agent']['prompt']['tool_ids']) == len(apply.TOOLS)


def test_a_dry_run_for_an_existing_agent_plans_an_update(capsys):
    assert apply.main(['--dry-run', '--agent-id', 'agent-123', '--voice-id', 'voice-9']) == 0
    steps = json.loads(capsys.readouterr().out)['plan']
    assert steps[1] == {'method': 'GET', 'path': '/v1/convai/agents/agent-123', 'why': 'read its current tool ids'}
    assert (steps[-1]['method'], steps[-1]['path']) == ('PATCH', '/v1/convai/agents/agent-123')
    assert steps[-1]['body']['conversation_config']['tts']['voice_id'] == 'voice-9'


def test_a_dry_run_against_a_different_session_maximum_is_refused(capsys):
    assert apply.main(['--dry-run', '--session-max', '120']) == 1
    assert 'max_duration_seconds is 300' in capsys.readouterr().err
