import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Material } from '../crexil/api';
import { AudioCell } from './AudioCell';
import { AudioInspector, formatLine, levelLine, voiceLine } from './AudioInspector';
import { audioMedium } from './medium';
import { audioUrl, player } from './player';
import { useAudition, type AuditionOptions } from './useAudition';

type Facts = NonNullable<Material['audio']>;
const facts = (extra: Partial<Facts> = {}): Facts => ({
  alignment: null, channels: 1, codec: 'pcm_s16le', container: 'wav', duration_s: 4.4, loudness_lufs: -18,
  peak_dbfs: -1.2, peaks: [0.1, 0.8, 0.4, 0.2], sample_rate: 24000, text: 'Steel remembers every strike.',
  voice: {provider: 'voice-runner', kind: 'designed', generated_voice_id: 'v-1'}, ...extra,
});
const take = (id: string, extra: Record<string, unknown> = {}) => ({
  id, payload: 'audio', available: true, engine: 'elevenlabs-v4', seed: 7, turn_idx: 2, slot_pos: 0, output_index: 0,
  identity_hidden: null, audio: facts(), ...extra,
}) as unknown as Material;

const press = (code: string, init: KeyboardEventInit = {}) => {
  const key = code === 'Space' ? ' ' : code.replace(/^(Digit|Key)/, '').toLowerCase();
  return fireEvent.keyDown(document.activeElement ?? document.body, {key, code, ...init});
};

let plays: string[] = [];
beforeEach(() => {
  plays = [];
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) {
    plays.push(this.src);
    this.dispatchEvent(new Event('play'));
    return Promise.resolve();
  });
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (this: HTMLMediaElement) {
    this.dispatchEvent(new Event('pause'));
  });
});
afterEach(() => {
  cleanup();
  player.release();
  vi.restoreAllMocks();
});

const played = () => plays.map(src => decodeURIComponent(src.split('/material/')[1]?.split('/')[0] ?? ''));

describe('the audio plate', () => {
  it('draws the waveform from the measured peaks and names the take', () => {
    render(<AudioCell node={take('n1')}/>);
    const plate = screen.getByRole('img', {name: '2A · audio · 4.4 s'});
    expect(plate.querySelector('svg')?.getAttribute('data-bars')).toBe('48');
  });
  it('stays silent to assistive technology where its face already names it', () => {
    const {container} = render(<AudioCell node={take('n1')} decorative/>);
    expect(container.firstElementChild).toHaveAttribute('aria-hidden', 'true');
    expect(screen.queryByRole('img')).toBeNull();
  });
  it('says it is an audio take when there are no peaks to draw', () => {
    render(<AudioCell node={take('n1', {audio: null})}/>);
    expect(screen.getByText('Audio take')).toBeInTheDocument();
  });
  it("draws a blind take's waveform at its own full height, so its picture never tells its level", () => {
    const quiet = take('q', {audio: facts({peaks: [0.1, 0.2, 0.1, 0.05]})});
    const tallest = (node: Material) => {
      const view = render(<AudioCell node={node}/>);
      const heights = [...view.container.querySelectorAll('rect')].map(rect => Number(rect.getAttribute('height')));
      view.unmount();
      return Math.max(...heights);
    };
    expect(tallest(quiet)).toBeCloseTo(19.2);
    expect(tallest({...quiet, identity_hidden: true} as Material)).toBeCloseTo(96);
  });
  it('shows which take is playing', async () => {
    render(<AudioCell node={take('n1')}/>);
    await act(() => player.play('n1'));
    expect(screen.getByRole('img', {name: '2A · audio · 4.4 s · playing'})).toBeInTheDocument();
  });
});

describe('the audio inspector', () => {
  it("states a revealed take's sound in words, and leaves its engine, seed and length to the record column", () => {
    render(<AudioInspector node={take('n1')}/>);
    expect(screen.getByText('Designed voice')).toBeInTheDocument();
    expect(screen.getByText('WAV · 16-bit PCM · 24 kHz · mono')).toBeInTheDocument();
    expect(screen.getByText('-18.0 LUFS')).toBeInTheDocument();
    for (const said of ['elevenlabs-v4', 'v-1', '7', 'Length', 'Engine', 'Seed']) expect(screen.queryByText(said)).toBeNull();
  });
  it('says no voice for a sound effect', () => {
    render(<AudioInspector node={take('n1', {verb: 'sfx', audio: facts({voice: null})})}/>);
    expect(screen.queryByText('Voice')).toBeNull();
    expect(screen.queryByText('Not reported')).toBeNull();
  });
  it('withholds the engine, voice and seed while the run is blind, and keeps the sound playable', async () => {
    const hidden = take('n1', {identity_hidden: true, engine: 'hidden', seed: null,
      audio: facts({voice: null, container: null, codec: null, sample_rate: null, channels: null})});
    render(<AudioInspector node={hidden}/>);
    expect(screen.getAllByText('Withheld until the run is revealed')).toHaveLength(2);
    expect(screen.queryByText('Seed')).toBeNull();
    expect(screen.queryByText('elevenlabs-v4')).toBeNull();
    expect(screen.queryByText('-18.0 LUFS')).toBeNull();                       // levels tell engines apart
    expect(screen.queryByText('-1.2 dBFS')).toBeNull();
    expect(screen.getAllByText('Level matched · figures shown after the reveal')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', {name: 'Play'}));
    await act(() => Promise.resolve());
    expect(played()).toEqual(['n1']);
  });
  it('follows the spoken words as the take plays', async () => {
    const alignment = {characters: [...'Steel'], character_start_times_seconds: [0, 0.1, 0.2, 0.3, 0.4]};
    render(<AudioInspector node={take('n1', {audio: facts({text: 'Steel', alignment})})}/>);
    await act(() => player.play('n1'));
    act(() => player.seek('n1', 0.25));
    const transcript = screen.getByRole('region', {name: 'Transcript'});
    expect(transcript.querySelector('span')?.textContent).toBe('Ste');
  });
  it('marks a loop only on the loaded take, and never opens a dialog', async () => {
    render(<AudioInspector node={take('n1')}/>);
    expect(screen.getByRole('button', {name: 'Loop in'})).toBeDisabled();
    await act(() => player.play('n1'));
    act(() => player.seek('n1', 1));
    fireEvent.click(screen.getByRole('button', {name: 'Loop in'}));
    act(() => player.seek('n1', 0.5));
    fireEvent.click(screen.getByRole('button', {name: 'Loop out'}));
    expect(screen.getByRole('status')).toHaveTextContent('A loop must end after it starts.');
    act(() => player.seek('n1', 3));
    fireEvent.click(screen.getByRole('button', {name: 'Loop out'}));
    expect(player.store.getState().loop).toEqual({start: 1, end: 3});
    expect(screen.queryByRole('dialog')).toBeNull();
  });
  it('plays and pauses this take with Space', async () => {
    render(<AudioInspector node={take('n1')}/>);
    press('Space');
    await act(() => Promise.resolve());
    expect(player.store.getState()).toMatchObject({nodeId: 'n1', playing: true});
    press('Space');
    expect(player.store.getState().playing).toBe(false);
  });
  it('keeps Space for a focused loop control, which Space presses', async () => {
    render(<AudioInspector node={take('n1')}/>);
    await act(() => player.play('n1'));
    act(() => player.seek('n1', 1));
    const loopIn = screen.getByRole('button', {name: 'Loop in'});
    loopIn.focus();
    expect(press('Space')).toBe(true);                                         // not prevented: the button keeps it
    expect(player.store.getState().playing).toBe(true);
  });
  it('moves the playhead a tenth with the arrows and a second with Page Up and Page Down', async () => {
    render(<AudioInspector node={take('n1')}/>);
    const scrub = screen.getByRole('slider', {name: 'Playhead'});
    expect(scrub).toHaveAttribute('step', '0.1');
    await act(() => player.play('n1'));
    act(() => player.seek('n1', 2));
    fireEvent.keyDown(scrub, {key: 'PageUp'});
    expect(player.store.getState().position).toBeCloseTo(3);
    fireEvent.keyDown(scrub, {key: 'PageDown'});
    fireEvent.keyDown(scrub, {key: 'PageDown'});
    expect(player.store.getState().position).toBeCloseTo(1);
  });
  it('words the level, voice and format lines plainly', () => {
    expect(levelLine({gainDb: -4.14, volume: 0.6, matched: true}, true)).toBe('Matched level');
    expect(levelLine(null)).toBe('As recorded · not level matched');
    expect(levelLine({gainDb: -4.14, volume: 0.6, matched: true})).toBe('Matched level · turned down 4.1 dB');
    expect(levelLine({gainDb: 0, volume: 1, matched: true})).toBe('Matched level · as recorded');
    expect(voiceLine({kind: 'saved', voice_id: 'abc'}, false)).toBe('Saved voice');
    expect(voiceLine({kind: 'cloned', reference_node_id: 'n9'}, false)).toBe('Cloned voice');
    expect(voiceLine(null, false)).toBe('Not reported');
    expect(formatLine(facts({channels: 2, sample_rate: 44100}), false)).toBe('WAV · 16-bit PCM · 44.1 kHz · stereo');
    expect(formatLine(facts({codec: 'alac'}), false)).toBe('WAV · alac · 24 kHz · mono');
  });
});

function Audition(props: AuditionOptions) {
  useAudition(props);
  return <input aria-label="note"/>;
}

describe('keyboard audition', () => {
  const takes = [take('a', {audio: facts({loudness_lufs: -14})}), take('img', {payload: 'image', audio: null}), take('c', {audio: facts({loudness_lufs: -20})})];

  it('plays the Nth take in grid order with a digit, from its start', async () => {
    render(<Audition takes={takes}/>);
    press('Digit3');
    await act(() => Promise.resolve());
    expect(played()).toEqual(['c']);
    press('Digit2');
    press('Digit9');
    await act(() => Promise.resolve());
    expect(played()).toEqual(['c']);
  });
  it('plays the louder take turned down to the quieter one', async () => {
    render(<Audition takes={takes}/>);
    press('Digit1');
    await act(() => Promise.resolve());
    expect(player.store.getState().levels.a.gainDb).toBeCloseTo(-6);
    const element = (HTMLMediaElement.prototype.play as unknown as {mock: {contexts: HTMLMediaElement[]}}).mock.contexts[0];
    expect(element.volume).toBeCloseTo(0.501, 3);
  });
  it('plays or pauses the focused take with Space, then falls back to the loaded take', async () => {
    render(<Audition takes={takes} focused={() => 'c'}/>);
    press('Space');
    await act(() => Promise.resolve());
    expect(player.store.getState()).toMatchObject({nodeId: 'c', playing: true});
    press('Space');
    expect(player.store.getState().playing).toBe(false);
  });
  it('never fires with a modifier held, while typing, or while not ready', async () => {
    let ready = false;
    render(<Audition takes={takes} ready={() => ready}/>);
    const blocked = press('Digit1');
    expect(blocked).toBe(true); // not prevented: another surface may own the key
    ready = true;
    press('Digit1', {ctrlKey: true});
    press('Space', {shiftKey: true});
    screen.getByRole('textbox', {name: 'note'}).focus();
    press('Digit1');
    await act(() => Promise.resolve());
    expect(plays).toEqual([]);
  });
  it('keeps a digit on its grid position across empty cells, which play nothing', async () => {
    const onPlay = vi.fn();
    render(<Audition takes={[null, takes[0], null, takes[2]]} onPlay={onPlay}/>);
    press('Digit1');
    press('Digit3');
    await act(() => Promise.resolve());
    expect(plays).toEqual([]);
    press('Digit4');
    await act(() => Promise.resolve());
    expect(played()).toEqual(['c']);
    expect(onPlay).toHaveBeenCalledWith(takes[2]);
  });
  it('rests the digits while the verb card is open, and keeps Space and X live', async () => {
    const card = (key: string) => key !== 'digit';
    render(<Audition takes={takes} ready={card} focused={() => 'a'} pair={() => ['a', 'c']}/>);
    press('Digit3');
    await act(() => Promise.resolve());
    expect(plays).toEqual([]);
    press('Space');
    await act(() => Promise.resolve());
    expect(player.store.getState()).toMatchObject({nodeId: 'a', playing: true});
    press('KeyX');
    await act(() => Promise.resolve());
    expect(player.store.getState().nodeId).toBe('c');
  });
  it('leaves Space to a focused button that is not an audio face', async () => {
    function Room() {
      useAudition({takes, focused: () => 'a'});
      return <><button>Esc · Project</button><button data-audio-face="">Act on 0A</button></>;
    }
    render(<Room/>);
    screen.getByRole('button', {name: 'Esc · Project'}).focus();
    expect(press('Space')).toBe(true);
    await act(() => Promise.resolve());
    expect(plays).toEqual([]);
    screen.getByRole('button', {name: 'Act on 0A'}).focus();
    expect(press('Space')).toBe(false);
    await act(() => Promise.resolve());
    expect(played()).toEqual(['a']);
  });
  it('switches between the two marked takes at the same playhead with X', async () => {
    render(<Audition takes={takes} pair={() => ['a', 'c']}/>);
    press('Digit1');
    await act(() => Promise.resolve());
    act(() => player.seek('a', 2));
    press('KeyX');
    await act(() => Promise.resolve());
    expect(player.store.getState()).toMatchObject({nodeId: 'c', position: 2});
    press('KeyX');
    await act(() => Promise.resolve());
    expect(player.store.getState().nodeId).toBe('a');
  });
  it('gives Space to an inspector opened over the grid, and back when it closes', async () => {
    const focused = () => 'c';
    function Room({inspecting}: {inspecting: boolean}) {
      return <><Audition takes={takes} focused={focused}/>{inspecting && <AudioInspector node={takes[0]}/>}</>;
    }
    const view = render(<Room inspecting={false}/>);
    view.rerender(<Room inspecting/>);
    press('Space');
    await act(() => Promise.resolve());
    expect(player.store.getState().nodeId).toBe('a');
    press('Digit3');
    await act(() => Promise.resolve());
    expect(played()).toEqual(['a']);
    view.rerender(<Room inspecting={false}/>);
    press('Space');
    await act(() => Promise.resolve());
    expect(player.store.getState()).toMatchObject({nodeId: 'c', playing: true});
  });
  it('stops playback when the grid that started it goes away', async () => {
    const view = render(<Audition takes={takes}/>);
    press('Digit1');
    await act(() => Promise.resolve());
    view.unmount();
    expect(player.store.getState().playing).toBe(false);
  });
});

describe('the audio medium entry', () => {
  it('draws a plate and offers no file types to the import picker', () => {
    expect(audioMedium).toMatchObject({noun: 'audio', hue: 'var(--medium-audio)', accept: [], inspectFrame: 'fullImage'});
    expect(audioMedium.hasPreview(take('n1'))).toBe(true);
    expect(audioMedium.hasPreview(take('n2', {audio: null}))).toBe(false);
    expect(audioUrl('a b')).toBe('/api/crexil/material/a%20b/audio');
  });
});
