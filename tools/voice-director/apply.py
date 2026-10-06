#!/usr/bin/env python3
"""Applies the voice director's configuration to an ElevenLabs account.

`agent.json` beside this file is the whole configuration: the agent, its prompt and
its six client tools. It holds no key and no account id. One command creates the
agent, or updates the one named by `--agent-id` (or ELEVENLABS_VOICE_AGENT_ID), and
prints the line to put in `.env`:

    python3 tools/voice-director/apply.py --dry-run          # validate and print the plan
    python3 tools/voice-director/apply.py                    # create or update, live

`--dry-run` never reads a key and never opens a connection. A live run reads
ELEVENLABS_API_KEY from the environment only, never from a file, never prints it,
and sends it in one header to the ElevenLabs API. Nothing retries silently (law 9):
a refused call stops the run with ElevenLabs' status and Crexil's plain words, and
everything already created is named so a second run updates it instead.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / 'agent.json'
API = 'https://api.elevenlabs.io'
TAG = 'crexil-voice-director'
SESSION_MAX_S = 300                       # Crexil's CREXIL_VOICE_SESSION_MAX_S default
# The panel's client-tool handlers, by name (cockpit/web/src/app/voice-director/tools.ts).
TOOLS = ('list_engines', 'get_project_summary', 'prepare_run', 'get_run_status', 'play_take', 'propose_pick')


class Refused(Exception):
    """A step ElevenLabs refused, or a configuration that may not be applied."""


def load(path: Path = CONFIG) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def problems(config: dict, session_max: int = SESSION_MAX_S) -> list[str]:
    """What keeps this configuration from being applied, in plain words; empty when none."""
    found = []
    agent = (config.get('conversation_config') or {}).get('agent') or {}
    prompt = agent.get('prompt') or {}
    if not config.get('name'):
        found.append('The agent needs a name.')
    if TAG not in (config.get('tags') or []):
        found.append(f'The agent must carry the tag {TAG!r}, so it can be recognised.')
    if not (prompt.get('prompt') or '').strip() or not prompt.get('llm'):
        found.append('The agent needs a system prompt and a language model.')
    if prompt.get('enable_parallel_tool_calls') is not False:
        found.append('Parallel tool calls must be off, so every action stays ordered and attributed.')
    if 'tool_ids' in prompt:
        found.append('tool_ids belong to an installation; apply fills them, the file never holds them.')
    longest = ((config.get('conversation_config') or {}).get('conversation') or {}).get('max_duration_seconds')
    if longest != session_max:
        found.append(f'max_duration_seconds is {longest}; it must equal the session maximum, {session_max} s.')
    settings = config.get('platform_settings') or {}
    if (settings.get('auth') or {}).get('enable_auth') is not True:
        found.append('The agent must require authentication, so only a server-minted token starts it.')
    if (settings.get('privacy') or {}).get('record_voice') is not False:
        found.append('Voice recording must be off.')
    tools = config.get('client_tools') or []
    names = [tool.get('name') for tool in tools]
    if sorted(names) != sorted(TOOLS) or len(names) != len(set(names)):
        found.append(f'The client tools must be exactly {", ".join(TOOLS)}; the file has {", ".join(map(str, names))}.')
    for tool in tools:
        found.extend(_tool_problems(tool))
    text = json.dumps(config)
    if 'xi-api-key' in text or '"agent_id"' in text or re.search(r'\bsk_[A-Za-z0-9]{8,}', text):
        found.append('The file must hold no key and no account id.')
    return found


def _tool_problems(tool: dict) -> list[str]:
    name, found = tool.get('name', '?'), []
    if tool.get('type') != 'client' or tool.get('expects_response') is not True:
        found.append(f'{name} must be a client tool that waits for its answer.')
    if not (tool.get('description') or '').strip():
        found.append(f'{name} needs a description.')
    parameters = tool.get('parameters') or {}
    properties = parameters.get('properties')
    if parameters.get('type') != 'object' or not isinstance(properties, dict):
        found.append(f"{name}'s parameters must be an object schema.")
        return found
    missing = set(parameters.get('required') or []) - set(properties)
    if missing:
        found.append(f'{name} requires parameters it does not define: {", ".join(sorted(missing))}.')
    for key, spec in properties.items():
        if not (spec.get('description') or '').strip():
            found.append(f'{name}.{key} needs a description.')
    return found


def agent_body(config: dict, tool_ids: list[str], voice_id: str | None = None) -> dict:
    """The agent's create or update body: the file minus its tools, plus the tool ids."""
    body = json.loads(json.dumps({key: value for key, value in config.items() if key != 'client_tools'}))
    body['conversation_config']['agent']['prompt']['tool_ids'] = list(tool_ids)
    if voice_id:
        body['conversation_config'].setdefault('tts', {})['voice_id'] = voice_id
    return body


def plan(config: dict, agent_id: str | None = None, voice_id: str | None = None) -> list[dict]:
    """The calls a live run would make, in order, with their bodies. No key appears."""
    steps = [{'method': 'GET', 'path': '/v1/convai/llm/list',
              'why': f"check that {config['conversation_config']['agent']['prompt']['llm']} is offered"}]
    if agent_id:
        steps.append({'method': 'GET', 'path': f'/v1/convai/agents/{agent_id}', 'why': 'read its current tool ids'})
    for tool in config['client_tools']:
        steps.append({'method': 'POST or PATCH', 'path': '/v1/convai/tools[/{tool_id}]',
                      'why': f"create or update the client tool {tool['name']}", 'body': {'tool_config': tool}})
    placeholder = [f'<id of {tool["name"]}>' for tool in config['client_tools']]
    steps.append({'method': 'PATCH' if agent_id else 'POST',
                  'path': f'/v1/convai/agents/{agent_id}' if agent_id else '/v1/convai/agents/create',
                  'why': 'update the agent' if agent_id else 'create the agent',
                  'body': agent_body(config, placeholder, voice_id)})
    return steps


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):  # a redirect would carry the key elsewhere
        return None


def call(method: str, path: str, key: str, body: dict | None = None, base: str = API) -> dict:
    """One ElevenLabs call with the key in its header. Raises Refused on any refusal."""
    data = None if body is None else json.dumps(body).encode('utf-8')
    request = urllib.request.Request(base + path, data=data, method=method,
                                     headers={'xi-api-key': key, 'Content-Type': 'application/json',
                                              'Accept': 'application/json'})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=20) as answer:
            raw = answer.read()
    except urllib.error.HTTPError as exc:
        words = {401: 'ElevenLabs refused the key.', 403: 'This key or plan cannot manage agents.',
                 404: 'ElevenLabs does not know this agent or tool.', 422: 'ElevenLabs refused the configuration.',
                 429: 'ElevenLabs asked to slow down. Run apply again in a moment.'}.get(exc.code, 'ElevenLabs refused the call.')
        detail = exc.read()[:400].decode('utf-8', 'replace')
        raise Refused(f'{method} {path}: {words} (HTTP {exc.code}) {detail}') from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise Refused(f'{method} {path}: ElevenLabs could not be reached ({exc}).') from None
    return json.loads(raw) if raw else {}


def _offered(listing: object, llm: str) -> bool:
    """Whether the model list names `llm`, wherever the listing nests it."""
    if isinstance(listing, str):
        return listing == llm
    if isinstance(listing, dict):
        return any(_offered(value, llm) for value in listing.values())
    if isinstance(listing, list):
        return any(_offered(value, llm) for value in listing)
    return False


def apply(config: dict, key: str, agent_id: str | None, voice_id: str | None, base: str = API, say=print) -> str:
    """Creates or updates the tools and the agent; returns the agent id."""
    llm = config['conversation_config']['agent']['prompt']['llm']
    if not _offered(call('GET', '/v1/convai/llm/list', key, base=base), llm):
        raise Refused(f'ElevenLabs does not offer {llm} to this account. Choose another model in agent.json.')
    known: dict[str, str] = {}
    if agent_id:
        current = call('GET', f'/v1/convai/agents/{agent_id}', key, base=base)
        for tool_id in ((current.get('conversation_config') or {}).get('agent') or {}).get('prompt', {}).get('tool_ids') or []:
            existing = call('GET', f'/v1/convai/tools/{tool_id}', key, base=base)
            name = (existing.get('tool_config') or {}).get('name')
            if name:
                known[name] = tool_id
    tool_ids = []
    for tool in config['client_tools']:
        if tool['name'] in known:
            call('PATCH', f"/v1/convai/tools/{known[tool['name']]}", key, {'tool_config': tool}, base)
            tool_ids.append(known[tool['name']])
            say(f"updated tool {tool['name']}")
        else:
            made = call('POST', '/v1/convai/tools', key, {'tool_config': tool}, base)
            tool_ids.append(made['id'])
            say(f"created tool {tool['name']}")
    body = agent_body(config, tool_ids, voice_id)
    if agent_id:
        call('PATCH', f'/v1/convai/agents/{agent_id}', key, body, base)
        say('updated the agent')
        return agent_id
    made = call('POST', '/v1/convai/agents/create', key, body, base)
    say('created the agent')
    return made['agent_id']


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply the voice director's configuration to ElevenLabs.")
    parser.add_argument('--dry-run', action='store_true', help='validate and print the plan; no key, no network')
    parser.add_argument('--config', type=Path, default=CONFIG, help='the configuration file (default: agent.json here)')
    parser.add_argument('--agent-id', default=None, help='update this agent (default: ELEVENLABS_VOICE_AGENT_ID, else create)')
    parser.add_argument('--voice-id', default=None, help="the director's own voice (default: the platform's)")
    parser.add_argument('--session-max', type=int, default=SESSION_MAX_S, help='CREXIL_VOICE_SESSION_MAX_S of the installation')
    args = parser.parse_args(argv)

    config = load(args.config)
    found = problems(config, args.session_max)
    if found:
        print('The configuration cannot be applied:', *found, sep='\n  ', file=sys.stderr)
        return 1
    if args.dry_run:
        print(json.dumps({'valid': True, 'plan': plan(config, args.agent_id, args.voice_id)}, indent=2))
        return 0

    key = os.environ.get('ELEVENLABS_API_KEY', '').strip()
    if not key:
        print('Set ELEVENLABS_API_KEY in the environment for a live run; nothing was sent.', file=sys.stderr)
        return 2
    agent_id = args.agent_id or os.environ.get('ELEVENLABS_VOICE_AGENT_ID', '').strip() or None
    try:
        made = apply(config, key, agent_id, args.voice_id)
    except Refused as exc:
        print(f'Stopped: {exc}', file=sys.stderr)
        return 3
    print(f'ELEVENLABS_VOICE_AGENT_ID={made}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
