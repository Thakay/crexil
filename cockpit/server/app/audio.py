"""The audio medium: what an audio take is, how it is measured and where it may go.

Audio lands as PCM WAV. A provider that answers with raw PCM is wrapped losslessly
with `wrap_pcm`; its own bytes and format stay in the execution receipt. Every
landed take is measured once at collection, by the server: duration, waveform
peaks for drawing, and integrated loudness (ITU-R BS.1770-4) for level-matched
listening. The measurement never changes the bytes.

Audio engines are rows of the capability matrix (`configs/engines.json`, law 12)
with payload `audio`. A row names its provider, and the provider names the
executor that reaches it (law 21: compatibility is by type, routing is by the
declared provider, never by the shape of an engine id). A row may carry the
provider's terms on its outputs; `refusal` states in plain words when such a take
may not feed another engine. Standard library only.
"""

from __future__ import annotations

import io
import json
import math
import wave
from array import array
from pathlib import Path

from . import config

PAYLOAD = 'audio'
MEDIA_TYPE = 'audio/wav'
VERBS = ('voice-design', 'speak', 'sfx')
PEAK_BINS = 256
# The providers an audio engine row may name, each with the executor that
# reaches it and the lane that executor serves (law 14: local takes turns).
PROVIDERS = {'elevenlabs': 'external', 'voice-runner': 'local'}

_BLOCK_S, _HOP_S = 0.4, 0.1
_ABSOLUTE_GATE, _RELATIVE_GATE = -70.0, -10.0


# --- measurement ---

def wrap_pcm(raw: bytes, *, sample_rate: int, channels: int = 1, sample_width: int = 2) -> bytes:
    """Little-endian PCM samples in a WAV container, sample for sample."""
    if sample_rate <= 0 or channels <= 0 or sample_width not in (1, 2, 3, 4):
        raise ValueError('The PCM format is not one this server can wrap.')
    if len(raw) % (channels * sample_width):
        raise ValueError('The PCM data ends partway through a sample.')
    out = io.BytesIO()
    with wave.open(out, 'wb') as target:
        target.setnchannels(channels)
        target.setsampwidth(sample_width)
        target.setframerate(sample_rate)
        target.writeframes(raw)
    return out.getvalue()


def _samples(data: bytes, width: int) -> array:
    """Signed integer samples, interleaved by channel."""
    if width == 1:                               # 8-bit WAV is unsigned
        return array('h', (b - 128 for b in data))
    if width == 2:
        return array('h', data)
    if width == 4:
        return array('i', data)
    return array('i', (int.from_bytes(data[i:i + 3], 'little', signed=True) for i in range(0, len(data), 3)))


def _shelf(fs: float) -> tuple[float, float, float, float, float]:
    """BS.1770 stage 1, the head's high shelf, designed for this sample rate."""
    gain, f0, q = 3.999843853973347, 1681.974450955533, 0.7071752369554196
    k = math.tan(math.pi * f0 / fs)
    vh = 10 ** (gain / 20)
    vb = vh ** 0.4996667741545416
    a0 = 1 + k / q + k * k
    return ((vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0,
            2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0)


def _highpass(fs: float) -> tuple[float, float, float, float, float]:
    """BS.1770 stage 2, the revised low-frequency B-curve high pass."""
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / fs)
    a0 = 1 + k / q + k * k
    return 1.0, -2.0, 1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0


def _segment_energy(channel: array, scale: float, fs: int) -> list[float]:
    """K-weighted energy of each whole 100 ms segment of one channel."""
    s0, s1, s2, s3, s4 = _shelf(fs)
    h0, h1, h2, h3, h4 = _highpass(fs)
    x1 = x2 = y1 = y2 = z1 = z2 = 0.0
    size = round(fs * _HOP_S)
    segments, acc, count = [], 0.0, 0
    for sample in channel:
        x = sample * scale
        y = s0 * x + s1 * x1 + s2 * x2 - s3 * y1 - s4 * y2
        x2, x1 = x1, x
        z = h0 * y + h1 * y1 + h2 * y2 - h3 * z1 - h4 * z2
        y2, y1 = y1, y
        z2, z1 = z1, z
        acc += z * z
        count += 1
        if count == size:
            segments.append(acc)
            acc, count = 0.0, 0
    return segments


def integrated_loudness(channels: list[array], scale: float, fs: int) -> float | None:
    """Gated integrated loudness in LUFS, or None when no 400 ms block is loud enough.

    Every channel weighs 1.0, which is exact for mono and stereo."""
    energies = [_segment_energy(channel, scale, fs) for channel in channels]
    size = round(fs * _HOP_S)
    per_block = round(_BLOCK_S / _HOP_S)
    count = min((len(e) for e in energies), default=0) - per_block + 1
    blocks = [sum(sum(e[j:j + per_block]) for e in energies) / (per_block * size) for j in range(max(count, 0))]

    def lufs(power: float) -> float:
        return -0.691 + 10 * math.log10(power) if power > 0 else -math.inf

    gated = [b for b in blocks if lufs(b) > _ABSOLUTE_GATE]
    if not gated:
        return None
    threshold = lufs(sum(gated) / len(gated)) + _RELATIVE_GATE
    kept = [b for b in gated if lufs(b) > threshold]
    return round(lufs(sum(kept) / len(kept)), 2) if kept else None


def inspect(data: bytes) -> dict:
    """Measured facts of a PCM WAV take. Raises ValueError in plain words otherwise."""
    try:
        with wave.open(io.BytesIO(data), 'rb') as source:
            channels, width = source.getnchannels(), source.getsampwidth()
            rate, frames = source.getframerate(), source.getnframes()
            raw = source.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise ValueError('This audio is not a PCM WAV file, so it could not be measured.') from exc
    if not channels or not rate:
        raise ValueError('This audio file names no channels or sample rate.')
    samples = _samples(raw, width)
    frames = len(samples) // channels
    scale = 1.0 / 128 if width == 1 else 1.0 / float(1 << (8 * width - 1))
    step = max(1, math.ceil(frames / PEAK_BINS)) * channels
    peaks = [round(max(map(abs, samples[i:i + step])) * scale, 4) for i in range(0, len(samples), step)]
    loudest = max(peaks, default=0.0)
    split = [samples[c::channels] for c in range(channels)]
    return {'container': 'wav', 'codec': 'pcm_u8' if width == 1 else f'pcm_s{8 * width}le',
            'sample_rate': rate, 'channels': channels, 'duration_s': round(frames / rate, 6),
            'loudness_lufs': integrated_loudness(split, scale, rate) if frames else None,
            'peak_dbfs': round(20 * math.log10(loudest), 2) if loudest > 0 else None,
            'peaks': peaks}


# --- the engine rows ---

_ROWS: dict[str, object] = {}


def engines() -> dict[str, dict]:
    """The matrix's audio rows by id, re-read when `configs/engines.json` changes."""
    path = Path(config.CONFIGS_DIR) / 'engines.json'
    try:
        stamp = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return {}
    if _ROWS.get('stamp') != stamp:
        try:
            rows = json.loads(path.read_text(encoding='utf-8')).get('engines') or []
        except (OSError, ValueError):
            return {}
        _ROWS.update(stamp=stamp, rows={row['id']: row for row in rows
                                        if isinstance(row, dict) and row.get('payload') == PAYLOAD and row.get('id')})
    return dict(_ROWS['rows'])


def route(engine: str) -> str | None:
    """The executor an audio engine reaches, or None when it is not an audio engine."""
    row = engines().get(engine)
    return row.get('provider') if row else None


def terms(row: dict) -> dict | None:
    """The provider's terms on this engine's outputs, as recorded on every take."""
    found = row.get('terms')
    return dict(found) if isinstance(found, dict) and found.get('no_ai_input') else None


def refusal(recorded: dict | None, provider: str) -> str | None:
    """Why a take with these recorded terms may not feed this provider's engine."""
    if not recorded or not recorded.get('no_ai_input'):
        return None
    if provider in recorded.get('allows', []):
        return None
    return recorded.get('reason') or 'This take\'s provider does not allow its output as input to another AI model.'
