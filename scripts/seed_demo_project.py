#!/usr/bin/env python3
"""Seed the dwarf blacksmith demo project through Crexil's HTTP API.

A client of the shared actions, like scripts/crexil.py: it never opens the store
(law 19), talks only to a loopback server named by --url or CREXIL_URL, and every
write names the participant given by --actor, which has no default (law 23).

One character brief runs through a character's first runs, in two phases:

  cast      sections on one Filmstrip row: the image casting on flat-priced hosted
            engines, a blind voice casting across ElevenLabs Voice Design and the
            local Qwen3-TTS, and two sound effects on ElevenLabs;
  continue  sections on the next row, from the takes a person or the operator names,
            recorded as their assessment with a reason: each line read in each chosen
            voice, on the engine that made the voice, and a 3D run from the chosen image.

Two steps belong to the operator: `limit` sets the project's spending limit, which
only a human sets, and `backup` saves the project as a portable archive through
scripts/crexil.py. Every step works on the project named "Dwarf blacksmith", or on the
one --project names, such as a copy restored from a backup.

Money (laws 6 and 22). Each run is prepared as a draft and reviewed, and its price is
printed as the review states it. Nothing is sent without --live USD, which must equal
the total the reviews reserve for the runs about to be sent. A run whose review is not
ready, such as one with an unknown price or one that would cross a limit, is never
sent. Paid work goes only into a project whose spending limit is set. A section named
with --leave-unsent stays a reviewed draft that the artist runs from the project.

Running a step again is safe. The project, each section and each choice are found
before anything is made, and each write carries a request id derived from what it does,
so a replay gets the saved answer and nothing happens twice. A section already
submitted is never submitted again. Runs are read through the compact run view until
they settle, and each take's label and charge are printed. Picks, reveals and delivery
stay the artist's (law 23).

  export CREXIL_URL=http://127.0.0.1:8000
  seed_demo_project.py --actor agent:Seed cast                    # prepare and review only
  seed_demo_project.py --actor human:NAME limit --usd 0.60 --reason "The demo's cap"
  seed_demo_project.py --actor agent:Seed cast --live 0.36708     # the total the review printed
  seed_demo_project.py --actor agent:Seed cast --leave-unsent "Blacksmith voice" --live 0.357
  seed_demo_project.py --actor agent:Seed continue --voice 1A --voice 1D.2 --image 0B \\
      --chosen-by human:NAME --reason "Warmest growl; the clearest silhouette"
  seed_demo_project.py --actor agent:Seed continue ... --live 0.01424
  seed_demo_project.py --actor agent:Seed backup dwarf-blacksmith.crexil.zip
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote, urlparse

# --- the plan: one character, in plain words ---

# The character's name: the project the seed works on, unless --project names another,
# and the title of the brief's own section, the image casting.
CHARACTER = 'Dwarf blacksmith'
BRIEF = ("Orrin Emberhand, the dwarf blacksmith of a mountain hold, for a game's forge level. Stout and broad, "
         'a braided copper beard tied with iron rings, a soot-dark leather apron, burn-scarred forearms and a '
         'hammer worn smooth at the grip. Gruff, proud of his craft, kind under the grumble.')
IMAGE_PROMPT = ('Full-body character concept of a stout dwarf blacksmith: braided copper beard tied with iron '
                'rings, soot on his cheeks, brass goggles pushed up on his brow, scorched leather apron over a '
                'rolled-sleeve linen shirt, burn-scarred forearms, a heavy forging hammer resting on one shoulder. '
                'Three-quarter view, the whole figure in frame, plain light-grey background, soft even light, '
                'stylized-realistic game art, a clear silhouette.')
VOICE_DESCRIPTION = ('A stout dwarf blacksmith in his sixties: a deep, gravelly baritone, warm and unhurried, '
                     'gruff but kind, with a smoky rasp and a little breath from working the bellows. '
                     'A close, clear studio recording.')
PREVIEW_LINE = ("Steel remembers every strike, and so do I. Bring me good ore and a little patience, "
                "and I'll forge you a blade worth its name.")
SOUNDS = (('Blacksmith footsteps', 'Heavy iron-shod boots on a stone forge floor: four slow, deliberate steps, '
                                   'close and dry.', 2.5),
          ('Forge strike', 'A heavy hammer blow on a glowing anvil: one bright metallic ring and a burst of '
                           'crackling sparks, punchy and short.', 2.0))
LINES = (('Bring me the ore', 'Bring me the ore, and mind the sparks.'),
         ('Good steel', 'Good steel takes patience. Bad steel takes fingers.'))

# Hosted image engines with a flat published price per image, the only kind a
# spending limit can reserve exactly; a token or megapixel price is unknown (law 22).
IMAGE_ENGINES = ('bytedance-seed/seedream-4.5', 'bytedance-seed/seedream-5-0-lite', 'recraft/recraft-v4.1')
VOICE_DESIGNERS = ('audio:voice-design:elevenlabs-voice-design', 'audio:voice-design:qwen3-tts')
SOUND_ENGINE = 'audio:sfx:elevenlabs-sfx'
# A voice speaks on the engine of the provider that made it: ElevenLabs' terms
# forbid its audio as input to another AI model, and v4 speaks ElevenLabs voices only.
SPEAKERS = {'elevenlabs': ('audio:speak:elevenlabs-v4', '0.02'), 'voice-runner': ('audio:speak:qwen3-tts', '0')}
MESH_ENGINE = 'mesh:generate:trellis2-4b'
IMAGE_ATTEMPTS, SOUND_ATTEMPTS, LINE_ATTEMPTS = 3, 3, 2
# Each run's own spending limit in US dollars, stated before its review (law 22).
IMAGE_CAP, VOICE_CAP, SOUND_CAP = '0.40', '0.05', '0.05'

# --- the client ---

MICRO = Decimal(1_000_000)
LOOPBACK = ('localhost', '127.0.0.1', '::1')
MOVING = {'queued', 'running', 'late'}       # a cell in these states may still change on its own
POLL_S = 2.0


class Refused(Exception):
    """A plain line for the operator: what stopped, and the next move."""


class ApiError(Exception):
    """The server's refusal: its status, its plain line and any named conflict."""

    def __init__(self, status: int, line: str, conflict: str | None = None):
        super().__init__(line)
        self.status, self.line, self.conflict = status, line, conflict


def parse_actor(text: str | None, flag: str) -> dict:
    kind, sep, name = (text or '').partition(':')
    if not sep or kind not in ('human', 'agent') or not name.strip() or len(name.strip()) > 120:
        raise Refused(f'Name the participant with {flag} as human:NAME or agent:NAME.')
    return {'kind': kind, 'name': name.strip()}


def name_of(actor: dict | None) -> str:
    return f"{actor['kind']}:{actor['name']}" if actor else 'someone'


def parse_usd(text: str) -> int:
    """A dollar amount as exact integer micro-dollars; sub-micro precision is refused."""
    try:
        value = Decimal(text.strip().removeprefix('$'))
    except InvalidOperation:
        raise Refused(f'{text} is not an amount in US dollars, such as 0.25.') from None
    micro = value * MICRO
    if not value.is_finite() or value < 0 or micro != micro.to_integral_value():
        raise Refused(f'{text} is not an amount in US dollars of zero or more, to at most six decimals.')
    return int(micro)


def micro_of(value) -> int:
    return int((Decimal(str(value or 0)) * MICRO).to_integral_value())


def usd(micro) -> str:
    """Exact dollars, never rounded to cents: sub-cent prices are real."""
    whole, _, cents = f'{(Decimal(micro) / MICRO).normalize():f}'.partition('.')
    return f'${whole}.{cents.ljust(2, "0")}'


def request_id(*parts: str) -> str:
    """A delivery id derived from what a write does: running a step again sends the
    same id with the same body, and the server answers with the work it already did."""
    return 'seed-' + str(uuid.uuid5(uuid.NAMESPACE_URL, 'crexil-seed/' + '/'.join(parts)))


def plural(count: int, noun: str) -> str:
    return f'{count} {noun}' + ('' if count == 1 else 's')


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


class Crexil:
    """One local Crexil server and the participant every write names."""

    def __init__(self, url: str, actor: dict):
        parsed = urlparse(url)
        if parsed.scheme != 'http' or parsed.hostname not in LOOPBACK or parsed.username or parsed.password:
            raise Refused('Use the local Crexil HTTP address, such as http://127.0.0.1:8000.')
        self.url, self.actor = url.rstrip('/'), actor

    def _send(self, method: str, path: str, body: dict | None = None):
        request = urllib.request.Request(self.url + '/api/crexil/' + path, method=method,
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read()).get('detail')
            except (ValueError, AttributeError):
                detail = None
            if isinstance(detail, list):
                detail = 'The request was not valid: ' + '; '.join(
                    f"{'.'.join(map(str, item.get('loc', [])[1:]))}: {item.get('msg')}" for item in detail
                    if isinstance(item, dict))
            line = detail if isinstance(detail, str) and detail else f'Crexil answered {exc.code}.'
            raise ApiError(exc.code, line, exc.headers.get('X-Crexil-Conflict')) from None

    def get(self, path: str):
        try:
            return self._send('GET', path)
        except OSError as exc:
            raise Refused(f'Crexil is not answering at {self.url} ({exc}). Start it, then run this again.') from None

    def write(self, method: str, path: str, values: dict, rid: str, actor: dict | None = None):
        """One shared action. A lost answer is asked once more with the same id and body;
        the server never does the work twice."""
        body = {**values, 'actor': actor or self.actor, 'request_id': rid}
        for attempt in (1, 2):
            try:
                return self._send(method, path, body)
            except OSError as exc:
                if attempt == 2:
                    raise Refused(f'Crexil did not answer ({exc}), so whether this was done is unknown. '
                                  'Run the same step again: it replays the same request.') from None
        return None


def run_path(direction_id: str) -> str:
    return f'directions/{quote(direction_id, safe="")}'


# --- the plan's sections ---

class Run(NamedTuple):
    """One planned section; its title is how the seed finds it again."""
    title: str
    action: str
    prompt: str
    engines: tuple             # ((operation id, attempts, controls), ...)
    cap: str = '0'             # the run's own spending limit in dollars; '0' sends free work only
    blind: bool = False
    sources: tuple = ()        # ((take id, role), ...)
    row: int | None = None     # the Filmstrip row; None joins the phase's shared row

    def setup(self, revisions: dict) -> dict:
        return {'action': self.action, 'prompt': self.prompt,
                'references': [{'node_id': node, 'contribution': '', 'role': role, 'strength': None}
                               for node, role in self.sources],
                'operations': [{'operation_id': op, 'revision': revisions.get(op, ''), 'count': count,
                                'controls': dict(controls)} for op, count, controls in self.engines],
                'spending_limit_usd': float(Decimal(self.cap)),
                'spending_policy': 'published_tariff' if Decimal(self.cap) > 0 else 'hard_cap',
                'blind': True if self.blind else None}


def cast_plan(image_engines) -> list[Run]:
    return [Run(CHARACTER, 'generate', IMAGE_PROMPT, tuple((op, IMAGE_ATTEMPTS, {}) for op in image_engines), IMAGE_CAP),
            Run('Blacksmith voice', 'voice-design', VOICE_DESCRIPTION,
                tuple((op, 1, {'preview_text': PREVIEW_LINE}) for op in VOICE_DESIGNERS), VOICE_CAP, blind=True),
            *(Run(title, 'sfx', words, ((SOUND_ENGINE, SOUND_ATTEMPTS, {'duration_seconds': seconds}),), SOUND_CAP)
              for title, words, seconds in SOUNDS)]


# --- reading the project ---

def find_project(api: Crexil, name: str, create: bool) -> dict:
    found = [p for p in api.get('projects') if p['name'] == name]
    if len(found) > 1:
        raise Refused(f'{len(found)} projects are named "{name}". Rename the ones the seed should leave alone, '
                      'then run this again.')
    if found:
        return found[0]
    if not create:
        raise Refused(f'No project is named "{name}". Run the cast step first, or name the project with --project.')
    made = api.write('POST', 'projects', {'name': name, 'brief': BRIEF}, request_id('project', name))
    print(f'Made the project "{name}" ({made["id"]}).')
    return made


def header(snapshot: dict) -> None:
    project, money = snapshot['project'], snapshot.get('accounting') or {}
    limit = (project.get('limits') or {}).get('spending_limit_usd')
    policy = (project.get('limits') or {}).get('spending_policy', 'hard_cap')
    print(f'Project "{project["name"]}" ({project["id"]}): '
          + (f'spending limit {usd(micro_of(limit))} at {"published tariffs" if policy == "published_tariff" else "a hard cap"}'
             if limit is not None else 'no spending limit')
          + f'; charged {usd(micro_of(money.get("charged_usd")))}, reserved {usd(micro_of(money.get("reserved_usd")))}.')


def takes_by_label(api: Crexil, snapshot: dict) -> dict:
    """Every landed take of the project by the grid's own label, with its section's row."""
    found = {}
    for direction in snapshot['directions']:
        if not direction['turn_ids']:
            continue
        view = api.get(run_path(direction['id']) + '/run')
        for cell in view['cells']:
            for take in cell['takes']:
                found[take['label']] = {**take, 'row': direction['row_idx'], 'section': direction['title']}
    return found


# --- preparing, reviewing and sending ---

def prepare(api: Crexil, project_id: str, directions: list, runs: list[Run], revisions: dict) -> list:
    """Find each planned section by its title, else make it, and bring the seed's own
    unsent drafts to the plan. Returns the (run, direction) pairs the seed works with;
    a section it leaves alone (discarded, ambiguous, or another participant's draft)
    is said in words."""
    by_title = {}
    for direction in directions:
        by_title.setdefault(direction['title'], []).append(direction)
    shared = next((d['row_idx'] for run in runs if run.row is None for d in by_title.get(run.title, [])
                   if d['state'] != 'discarded'), None)
    sections = []
    for run in runs:
        live = [d for d in by_title.get(run.title, []) if d['state'] != 'discarded']
        setup = run.setup(revisions)
        if len(live) > 1:
            print(f'{run.title}: {len(live)} sections carry this title, so the seed leaves them. Rename all but one.')
        elif not live and by_title.get(run.title):
            print(f'{run.title}: discarded in the project, so the seed leaves it.')
        elif not live:
            made = api.write('POST', f'projects/{project_id}/directions',
                             {'title': run.title, 'row_idx': run.row if run.row is not None else shared,
                              'setup': setup}, request_id(project_id, 'section', run.title))
            if run.row is None and shared is None:
                shared = made['row_idx']
            sections.append((run, made))
        elif live[0]['turn_ids'] or live[0]['state'] != 'draft':
            sections.append((run, live[0]))
        elif live[0].get('actor') != api.actor:
            print(f'{run.title}: a draft by {name_of(live[0].get("actor"))}, so the seed leaves it.')
        elif any(live[0]['setup'].get(key) != value for key, value in setup.items()):
            body = {'revision': live[0]['revision'], 'title': run.title, 'setup': setup}
            sections.append((run, api.write('PATCH', run_path(live[0]['id']), body,
                                            request_id(live[0]['id'], 'revise', str(live[0]['revision']), digest(body)))))
            print(f'{run.title}: its draft now follows the plan.')
        else:
            sections.append((run, live[0]))
    return sections


def sendable(review: dict) -> bool:
    return bool(review['ready']) and review['blocked_attempts'] == 0 and not review['errors']


def price_of(op: dict) -> str:
    """One operation's price per attempt as its review states it."""
    if 'reserved_micro_usd' in op or 'reserved_usd' in op:
        each = op.get('reserved_micro_usd', micro_of(op.get('reserved_usd')))
        return f'{usd(each)} per attempt' if each else 'free'
    return 'free' if op.get('maximum_usd_per_attempt') == 0 else 'price unknown'


def show_review(run: Run, review: dict) -> None:
    print(f'{run.title} · {run.action}' + (' · blind' if run.blind else ''))
    for op in review['snapshot']['operations']:
        print(f"  {op.get('label') or op['id']} × {op['count']}: {price_of(op)}"
              + ('' if op.get('ready') else ', not ready'))
    if not sendable(review):
        print('  Not ready, so it is not sent:')
        for error in review['errors']:
            print(f'    {error}')
        return
    reserved, setup = review['reserved_micro_usd'], review['snapshot']['setup']
    print(f"  Ready: {plural(review['attempts'], 'attempt')}; "
          + (f'reserves {usd(reserved)} at published tariffs, which are not a hard cap; ' if reserved
             else 'free: it reserves nothing; ')
          + f"the run's own limit is {usd(micro_of(setup['spending_limit_usd']))}"
          + (' at a hard cap' if setup['spending_policy'] == 'hard_cap' else '') + '.')


def submit(api: Crexil, direction: dict, review: dict) -> None:
    """Send one reviewed draft. When other work's spending moved since its review, it is
    reviewed once more and sent only if it is still ready at the same reservation."""
    path = run_path(direction['id']) + '/submit'
    body = {'revision': review['revision'], 'review_token': review['token']}
    try:
        api.write('POST', path, body, request_id(direction['id'], 'submit', str(review['revision']), review['token']))
    except ApiError as exc:
        if exc.status != 409 or exc.conflict != 'spending':
            raise
        again = api.get(run_path(direction['id']) + '/review')
        if not sendable(again) or again['reserved_micro_usd'] != review['reserved_micro_usd']:
            raise Refused(f'{direction["title"]}: spending moved since its review and it no longer goes as reviewed '
                          f'({exc.line}). Nothing was sent. Run this step again to read the new review.') from None
        body = {'revision': again['revision'], 'review_token': again['token']}
        api.write('POST', path, body, request_id(direction['id'], 'submit', str(again['revision']), again['token']))


def settled(view: dict) -> bool:
    return bool(view['submissions']) and not any(c['state'] in MOVING or c['retrying'] for c in view['cells'])


def charge(cell: dict) -> str:
    if cell.get('identity_hidden'):
        return 'cost after the reveal'
    if cell['charged_usd'] is not None:
        return f"charged {usd(micro_of(cell['charged_usd']))}" if cell['charged_usd'] else 'not charged'
    if not cell['committed_usd']:
        seconds = f", {cell['elapsed_ms'] / 1000:.1f} s" if cell['elapsed_ms'] else ''
        return 'free' + seconds
    return f"reserved {usd(micro_of(cell['committed_usd']))}, no charge reported yet"


def show_run(view: dict) -> None:
    print(f"{view['title']}: {view['summary']} Charged {usd(micro_of(view['charged_usd']))}, still reserved "
          f"{usd(micro_of(view['reserved_usd']))}; the run's limit {usd(micro_of(view['cap_usd']))}.")
    if any(s['blind'] and not s['revealed'] for s in view['submissions']):
        print('  Blind: engines and per-take costs show after the artist reveals the run.')
    for cell in view['cells']:
        takes = ', '.join(take['label'] + (f" {take['audio']['duration_s']:.1f} s" if take.get('audio') else '')
                          for take in cell['takes'])
        line = f"  {cell['label']:<5} {cell['state']:<8} {cell['engine']} · {charge(cell)}"
        print(line + (f' · takes {takes}' if takes else '') + (f" · {cell['cause']}" if cell['cause'] else ''))


def follow(api: Crexil, directions: list, live: bool, wait_s: float) -> list:
    """Read the sent runs through the compact run view; in a live step, until they
    settle or the wait ends, saying each change of a run's summary once."""
    deadline, said, views = time.monotonic() + wait_s, {}, {}
    while True:
        for direction in directions:
            view = views[direction['id']] = api.get(run_path(direction['id']) + '/run')
            if live and said.get(direction['id']) != view['summary']:
                said[direction['id']] = view['summary']
                print(f"  {view['title']}: {view['summary']}")
        if not live or all(settled(v) for v in views.values()) or time.monotonic() >= deadline:
            return list(views.values())
        time.sleep(POLL_S)


def run_phase(api: Crexil, project: dict, runs: list[Run], args, before_sending=None) -> int:
    """Prepare and review a phase's sections; with a confirmed total, send the ready
    drafts, except those the operator leaves unsent for the artist to run; then read
    every sent run. Returns the exit status."""
    live, unsent = args.live, set(args.leave_unsent or ())
    unknown = unsent - {run.title for run in runs}
    if unknown:
        raise Refused(f'No section of this step is titled {", ".join(sorted(unknown))}. Its sections: '
                      + ', '.join(run.title for run in runs) + '.')
    snapshot = api.get(f"projects/{project['id']}")
    header(snapshot)
    revisions = {op['id']: op['revision'] for op in api.get('operations')}
    sections = prepare(api, project['id'], snapshot['directions'], runs, revisions)
    ready, refused, sent = [], 0, []
    for run, direction in sections:
        if direction['turn_ids'] or direction['state'] != 'draft':
            print(f'{run.title}: sent before; it is never sent again'
                  + (', so it cannot be left unsent.' if run.title in unsent else '.'))
            sent.append(direction)
            continue
        review = api.get(run_path(direction['id']) + '/review')
        show_review(run, review)
        if not sendable(review):
            refused += 1
        elif run.title in unsent:
            print('  Left unsent, as asked: a reviewed draft the artist runs from the project, '
                  'its price on the Run button.')
        else:
            ready.append((run, direction, review))
    total = sum(review['reserved_micro_usd'] for _, _, review in ready)
    limit = (snapshot['project'].get('limits') or {}).get('spending_limit_usd')
    if ready and live is None:
        print(f'Dry run: nothing was sent. Ready to send: {plural(len(ready), "run")}, reserving {usd(total)} in all; '
              f'send with --live {(Decimal(total) / MICRO).normalize():f}.')
        if total and limit is None:
            print('Paid work needs the project spending limit the artist sets first: '
                  'seed_demo_project.py --actor human:NAME limit --usd AMOUNT --reason "WHY".')
    elif ready:
        if live != total:
            raise Refused(f'The reviews reserve {usd(total)} for {plural(len(ready), "run")}; you confirmed {usd(live)}. '
                          'Nothing was sent. Read the prices above, then confirm their total.')
        if total and limit is None:
            raise Refused('This project has no spending limit, so no paid work goes. The artist sets one: '
                          'seed_demo_project.py --actor human:NAME limit --usd AMOUNT --reason "WHY". Nothing was sent.')
        if before_sending:
            before_sending()
        for run, direction, review in ready:
            submit(api, direction, review)
            reserved = review['reserved_micro_usd']
            print(f'{run.title}: sent' + (f', reserving {usd(reserved)}.' if reserved else '; free.'))
            sent.append(direction)
    elif live is not None:
        print('Nothing left to send in this step.')
    if sent:
        print('Waiting for the runs to settle.' if live is not None else 'Runs sent before, as they stand:')
        for view in follow(api, sent, live is not None, args.wait_s):
            show_run(view)
            if not settled(view):
                print('  Still in flight: run this step again later to read it; nothing is sent twice.')
    return 1 if refused else 0


# --- the steps ---

def cast(api: Crexil, args) -> int:
    project = find_project(api, args.project, create=True)
    runs = cast_plan(args.image_engine or IMAGE_ENGINES)
    status = run_phase(api, project, runs, args)
    if args.live is not None:
        unsent = ', '.join(f'"{title}"' for title in args.leave_unsent or ())
        print('Next: ' + (f'the artist runs {unsent} from the project; then ' if unsent else '')
              + 'the artist auditions and picks in the grid and reveals "Blacksmith voice"; then name the takes '
              'to continue from: continue --voice LABEL --image LABEL --chosen-by PARTICIPANT --reason "WHY".')
    return status


def resolve(takes: dict, label: str, payload: str) -> dict:
    take = takes.get(label)
    if take is None:
        raise Refused(f'No landed take in this project is labelled {label}. Use a label from the grid, such as 0B.')
    if take['payload'] != payload:
        raise Refused(f'{label} is {take["payload"]}, not {payload}. Name {payload} takes here.')
    if take.get('identity_hidden'):
        raise Refused(f'{label} belongs to a blind run that is not revealed yet. The artist reveals '
                      f'"{take["section"]}" in its grid room; then run this again.')
    if not take['available']:
        raise Refused(f"{label}'s file is unavailable. Restore it, or choose another take.")
    return take


def continue_from(api: Crexil, args) -> int:
    if not args.voice and not args.image:
        raise Refused('Name the takes to continue from: --voice LABEL for each voice, --image LABEL for the image.')
    chooser = parse_actor(args.chosen_by, '--chosen-by')
    project = find_project(api, args.project, create=False)
    takes = takes_by_label(api, api.get(f"projects/{project['id']}"))
    runs, chosen = [], []
    for label in dict.fromkeys(args.voice):
        take = resolve(takes, label, 'audio')
        provider = ((take.get('audio') or {}).get('voice') or {}).get('provider')
        if provider not in SPEAKERS:
            raise Refused(f'{label} records no voice the seed can speak with. Name a take from "Blacksmith voice".')
        engine, cap = SPEAKERS[provider]
        chosen.append(take['node_id'])
        runs += [Run(f'Line · {label} · {short}', 'speak', line, ((engine, LINE_ATTEMPTS, {}),), cap,
                     sources=((take['node_id'], 'voice'),), row=take['row'] + 1) for short, line in LINES]
    if args.image:
        take = resolve(takes, args.image, 'image')
        chosen.append(take['node_id'])
        runs.append(Run(f'Mesh · {args.image}', 'mesh', '', ((MESH_ENGINE, 1, {}),),
                        sources=((take['node_id'], 'image'),), row=take['row'] + 1))

    def record_choice():
        node_ids = sorted(chosen)
        made = api.write('POST', f"projects/{project['id']}/assessments", {'reason': args.reason, 'node_ids': node_ids},
                         request_id(project['id'], 'choice', digest([node_ids, chooser, args.reason])), chooser)
        print(f"Recorded {name_of(made.get('actor'))}'s choice of {', '.join(dict.fromkeys(args.voice))}"
              + (f'{", " if args.voice else ""}{args.image}' if args.image else '') + f': {made.get("reason")}')

    status = run_phase(api, project, runs, args, record_choice)
    if args.live is not None:
        print('Next: the artist picks and delivers in the project; back it up with: backup FILE.')
    return status


def set_limit(api: Crexil, args) -> int:
    if api.actor['kind'] != 'human':
        raise Refused("A project's spending limit is the artist's to set: it decides how much money may move. "
                      'Run this step with --actor human:NAME.')
    if not args.reason.strip():
        raise Refused('Say why with --reason, such as whose cap this is.')
    micro = parse_usd(args.usd)
    project = find_project(api, args.project, create=True)
    current = project.get('limits') or {}
    limits = {'attempt_limit': current.get('attempt_limit'), 'spending_limit_usd': float(Decimal(micro) / MICRO),
              'spending_policy': 'published_tariff'}
    if all(current.get(key) == value for key, value in limits.items()):
        print(f'The project spending limit is already {usd(micro)} at published tariffs. Nothing changed.')
        return 0
    body = {'revision': project['revision'], 'name': project['name'], 'brief': project['brief'],
            'instructions': project['instructions'], 'limits': limits}
    api.write('PATCH', f"projects/{project['id']}", body,
              request_id(project['id'], 'limit', str(project['revision']), digest(limits)))
    print(f'{name_of(api.actor)} set the project spending limit to {usd(micro)} at published tariffs, which '
          f'reserve by list price and are not a hard cap. Reason: {args.reason.strip()}')
    return 0


def backup(api: Crexil, args) -> int:
    project = find_project(api, args.project, create=False)
    target = Path(args.file).resolve()
    if target.exists():
        raise Refused(f'{target} exists. A backup never overwrites a file; name a new one.')
    # The command-line client downloads the archive and checks it against its receipt.
    cli = Path(__file__).resolve().with_name('crexil.py')
    return subprocess.run([sys.executable, str(cli), '--url', api.url, '--actor', name_of(api.actor),
                           '--request-id', request_id(project['id'], 'backup', str(target)),
                           'backup', project['id'], str(target)], check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url', default=os.environ.get('CREXIL_URL'), help='The local Crexil address (CREXIL_URL)')
    parser.add_argument('--actor', help='human:NAME or agent:NAME; every write names it, and there is no default')
    parser.add_argument('--project', default=CHARACTER, metavar='NAME',
                        help=f'The project, by its exact name (default: {CHARACTER}), such as a restored copy')
    steps = parser.add_subparsers(dest='step', required=True)
    cast_step = steps.add_parser('cast', help='Images, a blind voice casting and two sound effects from the brief')
    cast_step.add_argument('--image-engine', action='append', metavar='ID',
                           help='A hosted image engine with a flat per-image price; repeat for each '
                                f'(default: {", ".join(IMAGE_ENGINES)})')
    continue_step = steps.add_parser('continue', help='Line reads in chosen voices and 3D from a chosen image')
    continue_step.add_argument('--voice', action='append', default=[], metavar='LABEL',
                               help='A voice take, by its label in the grid; repeat for each voice')
    continue_step.add_argument('--image', metavar='LABEL', help='The image take for 3D, by its label in the grid')
    continue_step.add_argument('--chosen-by', required=True, metavar='PARTICIPANT',
                               help='human:NAME or agent:NAME: who chose these takes')
    continue_step.add_argument('--reason', required=True, help='Why these takes, in one line')
    for step in (cast_step, continue_step):
        step.add_argument('--live', metavar='USD', help='Send the ready runs, confirming the total their reviews '
                                                        'reserve; without it nothing is sent')
        step.add_argument('--wait-s', type=float, default=1200, help='How long a live step waits for its runs')
        step.add_argument('--leave-unsent', action='append', metavar='SECTION',
                          help="Prepare and review this section, by its title, but never send it: the artist "
                               'runs it from the project; repeat for each')
    limit_step = steps.add_parser('limit', help="The project's spending limit; a human act")
    limit_step.add_argument('--usd', required=True, help='The limit in US dollars, at published-tariff budgeting')
    limit_step.add_argument('--reason', required=True, help='Why, such as whose cap this is')
    backup_step = steps.add_parser('backup', help='The project as a portable archive')
    backup_step.add_argument('file', help='A new local file; an existing file is never overwritten')
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)   # progress and refusals keep their order in a log
    try:
        if not args.url:
            raise Refused('Name the Crexil server with --url or CREXIL_URL, such as http://127.0.0.1:8000.')
        api = Crexil(args.url, parse_actor(args.actor, '--actor'))
        if getattr(args, 'live', None) is not None:
            args.live = parse_usd(args.live)
        return {'cast': cast, 'continue': continue_from, 'limit': set_limit, 'backup': backup}[args.step](api, args)
    except Refused as exc:
        print(exc, file=sys.stderr)
        return 1
    except ApiError as exc:
        print(f'Crexil refused: {exc.line}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
