// The audio surface's public parts. The media registry holds `audioMedium` as MEDIA.audio; a
// surface calls useAudition with its take positions in grid order, and the positions its digits
// number, and reads playback through usePlayer.
export { AudioCell } from './AudioCell';
export { AudioInspector, formatLine, levelLine, voiceLine } from './AudioInspector';
export { MAX_ATTENUATION_DB, matchLevels, type Level } from './level';
export { audioMedium } from './medium';
export { audioUrl, createPlayer, player, usePlayer, type Loop, type MediaLike, type PlayerState } from './player';
export { isAudible, useAudioKeys, useAudition, type AuditionKey, type AuditionOptions } from './useAudition';
export { Waveform } from './Waveform';
export { bars, clock, length, spokenSplit } from './display';
