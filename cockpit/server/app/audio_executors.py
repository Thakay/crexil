"""The executors that reach audio providers, found by provider name.

Each provider in `audio.PROVIDERS` has one adapter module beside this one:
`elevenlabs` (hosted) and `local_voice` (the local voice runner). An adapter
module exposes `build_executor() -> Executor`; its executor's `name` is the
provider name and its `lane` is the provider's lane. Optionally it exposes
`async def connection(stack) -> dict` for the setup overview, using the keys of
`ConnectionView`.

An adapter that is not built yet is represented by a refusing executor, so
nothing reaches a provider until its adapter lands, and the operation catalogue
says so in plain words. A module that exists but fails to import is a defect and
raises loudly instead of being reported as missing.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import ModuleType

import httpx

from . import audio, config
from .executors import EngineFailure, Reconciliation

MODULES = {'elevenlabs': 'elevenlabs', 'voice-runner': 'local_voice'}
LABELS = {'elevenlabs': 'ElevenLabs', 'voice-runner': 'The local voice runner'}


@dataclass
class NotBuiltExecutor:
    """A refusal boundary for a provider whose adapter has not landed."""
    name: str
    lane: str
    not_built: bool = True

    @property
    def message(self) -> str:
        return f'{LABELS.get(self.name, self.name)} adapter is not built yet. Nothing was sent.'

    async def open(self):
        pass

    async def close(self):
        pass

    async def start(self, _req):
        raise EngineFailure('model_down', self.message, charged_usd=0)

    async def wait(self, _handle):
        raise EngineFailure('model_down', self.message, charged_usd=0)

    async def cancel(self, _handle):
        pass

    async def reconcile(self, _slot, *, request=None):  # noqa: ARG002
        return Reconciliation('failed', cause=self.message, failure_kind='model_down')

    async def verify_charge(self, _job_ref):
        return None


def adapter(provider: str) -> ModuleType | None:
    """The provider's adapter module, or None while it is not built."""
    name = f'{__package__}.{MODULES[provider]}'
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        return None


def build() -> list:
    """One executor per audio provider: the adapter's, or a refusal."""
    executors = []
    for provider, lane in audio.PROVIDERS.items():
        module = adapter(provider)
        executor = module.build_executor() if module else NotBuiltExecutor(provider, lane)
        if executor.name != provider or executor.lane != lane:
            raise RuntimeError(f'The {provider} adapter must build an executor named {provider!r} on the {lane} lane.')
        executors.append(executor)
    return executors


def ready(executor) -> bool:
    """Whether this executor can reach its provider at all (built, not a refusal)."""
    return executor is not None and not getattr(executor, 'not_built', False)


async def connection(provider: str, stack) -> dict:
    """The setup overview's line for one audio provider: whether a key is present
    (never the key) or whether the runner answers. An adapter may refine it."""
    module = adapter(provider)
    if module is not None and callable(getattr(module, 'connection', None)):
        return await module.connection(stack)
    label = LABELS[provider]
    line = {'provider': provider, 'label': label, 'ok': False, 'key_source': None}
    if config.CREXIL_PREVIEW:
        return {**line, 'detail': 'Disconnected in this isolated preview.'}
    pending = '' if module else ' Its adapter is not built yet.'
    if provider == 'elevenlabs':
        if not config.ELEVENLABS_API_KEY:
            return {**line, 'detail': 'No key. Set ELEVENLABS_API_KEY in .env, then restart.' + pending}
        return {**line, 'ok': True, 'key_source': 'env',
                'detail': 'A key is set in the environment. It has not been checked.' + pending}
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            answered = (await client.get(f'{config.VOICE_RUNNER_URL}/health')).status_code == 200
    except httpx.HTTPError:
        answered = False
    return {**line, 'ok': answered,
            'detail': (f'Answering at {config.VOICE_RUNNER_URL}.' if answered else
                       f'Not answering at {config.VOICE_RUNNER_URL}. Start the voice runner.') + pending}
