// The voice director's public parts. A project surface mounts the panel once:
//   <VoiceDirectorPanel projectId={id}/>
// Open, it is a column at the right of the work, and every surface keeps clear of
// var(--director-inset, 0px), which the column sets while it is open. It loads its
// conversation code only when opened.
export { VoiceDirectorPanel, type VoiceDirectorPanelProps } from './VoiceDirectorPanel';
export { DIRECTOR } from './door';
export { TOOL_NAMES } from './tools';
