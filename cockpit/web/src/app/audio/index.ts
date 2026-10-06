// The audio surface's public parts. A surface registers `audioMedium` as MEDIA.audio, calls
// useAudition with a run's takes in grid order, and reads playback through usePlayer.
export { AudioCell } from './AudioCell';
export { AudioInspector, formatLine, levelLine, voiceLine } from './AudioInspector';
export { MAX_ATTENUATION_DB, matchLevels, type Level } from './level';
export { audioMedium } from './medium';
export { audioUrl, createPlayer, player, usePlayer, type Loop, type MediaLike, type PlayerState } from './player';
export { isAudible, useAudioKeys, useAudition, type AuditionOptions } from './useAudition';
export { Waveform } from './Waveform';
export { bars, clock, length, spokenSplit } from './display';
