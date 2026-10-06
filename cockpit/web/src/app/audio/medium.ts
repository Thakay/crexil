// The audio medium's entry for the client's media registry: the plate a face draws, the
// inspection body, the words a face adds to its name and the measured length the record
// lists. The registry holds it as MEDIA.audio, beside the --medium-audio hue token. Audio
// is not imported from files, so it offers no file types to the picker.
import type { MediumView } from '../crexil/media';
import { AudioCell } from './AudioCell';
import { AudioInspector } from './AudioInspector';
import { clock, length } from './display';

export const audioMedium: MediumView = {
  noun: 'audio',
  hue: 'var(--medium-audio)',
  accept: [],
  hasPreview: node => Boolean(node.audio?.peaks.length),
  placeholder: 'Audio take',
  Plate: AudioCell,
  Inspect: AudioInspector,
  inspectFrame: 'fullImage',
  recordedDescription: false,
  describe: node => ['audio', length(node.audio?.duration_s)].filter(Boolean).join(', '),
  facts: node => node.audio ? [['Length', clock(node.audio.duration_s)]] : [],
};
