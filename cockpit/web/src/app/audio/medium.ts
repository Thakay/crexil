// The audio medium's entry for the client's media registry: the plate a face draws and the
// inspection body. A surface registers it as MEDIA.audio together with the --medium-audio
// hue token. Audio is not imported from files, so it offers no file types to the picker.
import type { MediumView } from '../crexil/media';
import { AudioCell } from './AudioCell';
import { AudioInspector } from './AudioInspector';

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
};
