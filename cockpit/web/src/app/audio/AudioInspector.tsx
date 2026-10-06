// Inspection of one audio take: play, pause, scrub, a loop region, the spoken words as they
// pass, and the sound's own measured facts (law 17), in words: its level, format and voice.
// The record column beside it holds the engine, the seed and the length, once each. While
// its run is blind, the voice, any withheld format facts and every level figure stay
// hidden, since levels differ by engine; the sound stays playable at its matched level. It lives inside the inspection layer and never
// opens a dialog of its own (law 2). Space plays and pauses this take, except on a focused
// control, which Space presses; the arrow keys move the playhead a tenth of a second and Page
// Up and Page Down a second.
import { useEffect, useState, type KeyboardEvent } from 'react';
import type { Material } from '../crexil/api';
import { spaceActivates, useCommandKey } from '../crexil/keys';
import s from './audio.module.css';
import type { Level } from './level';
import { player, usePlayer } from './player';
import { useAudioKeys } from './useAudition';
import { clock, spokenSplit } from './display';
import { Waveform } from './Waveform';

type Facts = NonNullable<Material['audio']>;
const WITHHELD = 'Withheld until the run is revealed';
const LEVEL_WITHHELD = 'Level matched · figures shown after the reveal';

/** How the take sounds when compared: turned down to the shared level, or as recorded. A
 *  blind take never shows by how much, which would tell engines apart. */
export function levelLine(level: Level | null, hidden = false): string {
  if (!level || !level.matched) return 'As recorded · not level matched';
  if (hidden) return 'Matched level';
  if (level.gainDb > -0.05) return 'Matched level · as recorded';
  return `Matched level · turned down ${(-level.gainDb).toFixed(1)} dB`;
}

/** The voice an engine reported, in words; its provider's own voice id stays in the record. */
export function voiceLine(voice: Facts['voice'], hidden: boolean): string {
  if (hidden) return WITHHELD;
  const kind = voice && typeof voice.kind === 'string' ? voice.kind : '';
  if (kind === 'designed') return 'Designed voice';
  if (kind === 'saved') return 'Saved voice';
  if (kind === 'cloned') return 'Cloned voice';
  return voice ? 'Reported by the engine' : 'Not reported';
}

const CODECS: Readonly<Record<string, string>> = {pcm_s16le: '16-bit PCM', pcm_s24le: '24-bit PCM', pcm_s32le: '32-bit PCM',
  pcm_f32le: '32-bit float PCM', mp3: 'MP3', opus: 'Opus', vorbis: 'Vorbis', aac: 'AAC', flac: 'FLAC'};

/** The container, codec, rate and channels, as far as they are known, in words. */
export function formatLine(facts: Facts | null, hidden: boolean): string {
  if (!facts) return 'Not measured';
  const channels = facts.channels === null ? '' : facts.channels === 1 ? 'mono' : facts.channels === 2 ? 'stereo' : `${facts.channels} channels`;
  const parts = [facts.container?.toUpperCase() ?? '', facts.codec ? CODECS[facts.codec] ?? facts.codec : '',
    facts.sample_rate === null ? '' : `${facts.sample_rate / 1000} kHz`, channels].filter(Boolean);
  if (parts.length) return parts.join(' · ');
  return hidden ? WITHHELD : 'Not reported';
}

export function AudioInspector({node}: {node: Material}) {
  const facts = node.audio ?? null;
  const hidden = node.identity_hidden === true;
  const total = facts?.duration_s ?? 0;
  const loaded = usePlayer(state => state.nodeId === node.id);
  const playing = usePlayer(state => state.nodeId === node.id && state.playing);
  const position = usePlayer(state => (state.nodeId === node.id ? state.position : 0));
  const loop = usePlayer(state => (state.nodeId === node.id ? state.loop : null));
  const error = usePlayer(state => (state.nodeId === node.id ? state.error : null));
  const level = usePlayer(state => state.levels[node.id] ?? null);
  const [hint, setHint] = useState<string | null>(null);
  const owner = useAudioKeys();

  useCommandKey('space', () => void player.toggle(node.id, {duration: total}), () => owner() && node.available, spaceActivates);
  useEffect(() => () => player.pauseIf([node.id]), [node.id]);

  if (!node.available) return <p>This take's audio is unavailable. Its record remains.</p>;

  const mark = (edge: 'start' | 'end') => {
    const span = edge === 'start' ? {start: position, end: loop?.end ?? total} : {start: loop?.start ?? 0, end: position};
    setHint(player.setLoop(span) ? null : 'A loop must end after it starts. Move the playhead and mark again.');
  };
  const words = facts?.text ?? '';
  const {spoken, rest} = spokenSplit(words, facts?.alignment, loaded ? position : -1);
  const share = (seconds: number) => (total > 0 ? seconds / total : 0);
  // Page Up and Page Down move the playhead a whole second; the arrows keep the range's tenth.
  const leap = (event: KeyboardEvent<HTMLInputElement>) => {
    const by = event.key === 'PageUp' ? 1 : event.key === 'PageDown' ? -1 : 0;
    if (!by) return;
    event.preventDefault();
    player.seek(node.id, Math.min(total, Math.max(0, position + by)), total);
  };

  return (
    <div className={s.inspector}>
      <div className={s.stage}>
        <Waveform peaks={facts?.peaks ?? []} count={128} progress={loaded ? share(position) : 0} normalized={hidden}
          loop={loop ? {start: share(loop.start), end: share(loop.end)} : null}/>
        <input type="range" className={s.scrub} min={0} max={total} step={0.1} value={Math.min(position, total)}
          disabled={total <= 0} aria-label="Playhead" aria-valuetext={`${clock(position)} of ${clock(total)}`}
          onKeyDown={leap} onChange={event => player.seek(node.id, Number(event.target.value), total)}/>
      </div>
      <div className={s.transport}>
        <button type="button" onClick={() => void player.toggle(node.id, {duration: total})}>{playing ? 'Pause' : 'Play'}</button>
        <span className={s.clock}>{clock(position)} / {clock(total)}</span>
        <span className={s.spacer}/>
        <button type="button" disabled={!loaded} onClick={() => mark('start')}>Loop in</button>
        <button type="button" disabled={!loaded} onClick={() => mark('end')}>Loop out</button>
        <button type="button" disabled={!loop} onClick={() => {player.setLoop(null); setHint(null);}}>Clear loop</button>
      </div>
      {loop && <p className={s.note}>Looping {clock(loop.start)} to {clock(loop.end)}</p>}
      {hint && <p className={s.note} role="status">{hint}</p>}
      {error && <p className={s.error} role="alert">{error}</p>}
      {words && (
        <section className={s.transcript} aria-label="Transcript">
          <h3>Spoken</h3>
          <p><span className={s.spoken}>{spoken}</span><span className={s.rest}>{rest}</span></p>
        </section>
      )}
      <dl className={s.facts}>
        <dt>Loudness</dt><dd>{hidden ? LEVEL_WITHHELD : facts?.loudness_lufs == null ? 'Too short or too quiet to measure' : `${facts.loudness_lufs.toFixed(1)} LUFS`}</dd>
        <dt>Peak</dt><dd>{hidden ? LEVEL_WITHHELD : facts?.peak_dbfs == null ? 'Not measured' : `${facts.peak_dbfs.toFixed(1)} dBFS`}</dd>
        <dt>Plays at</dt><dd>{levelLine(level, hidden)}</dd>
        <dt>Format</dt><dd>{formatLine(facts, hidden)}</dd>
        {node.verb !== 'sfx' && <><dt>Voice</dt><dd>{voiceLine(facts?.voice ?? null, hidden)}</dd></>}
      </dl>
    </div>
  );
}
