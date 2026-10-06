// An audio take's plate on a face, a thumb or a source chip: its waveform, its length and
// whether it is playing. Faces are buttons, so the plate holds no controls of its own;
// playback comes from the audition keys and the inspector.
import { label, type Material } from '../crexil/api';
import s from './audio.module.css';
import { usePlayer } from './player';
import { length } from './display';
import { Waveform } from './Waveform';

function PlayingMark() {
  return (
    <svg className={s.playingMark} viewBox="0 0 12 12" aria-hidden="true" focusable="false">
      <path d="M2 4.5v3M5 2.5v7M8 4v4M11 5.5v1" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" fill="none"/>
    </svg>
  );
}

export function AudioCell({node, decorative = false}: {node: Material; decorative?: boolean}) {
  const facts = node.audio ?? null;
  const playing = usePlayer(state => state.nodeId === node.id && state.playing);
  const progress = usePlayer(state => {
    if (state.nodeId !== node.id) return 0;
    const total = state.duration || facts?.duration_s || 0;
    return total > 0 ? state.position / total : 0;
  });
  const span = length(facts?.duration_s);
  const name = [label(node), 'audio', span, playing ? 'playing' : ''].filter(Boolean).join(' · ');
  const naming = decorative ? {'aria-hidden': true as const} : {role: 'img', 'aria-label': name};
  return (
    <div className={s.cell} data-playing={playing ? 'true' : undefined} {...naming}>
      {facts && facts.peaks.length > 0
        ? <Waveform peaks={facts.peaks} count={48} progress={progress} className={s.cellWave}/>
        : <span className={s.placeholder}>Audio take</span>}
      {span && <span className={s.duration}>{span}</span>}
      {playing && <PlayingMark/>}
    </div>
  );
}
