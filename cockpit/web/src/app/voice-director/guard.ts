// What the voice director's language model may read: facts, ids, labels and reasons, never
// media or an address that leads to media. ElevenLabs' policy forbids its output as input to
// any machine learning, and the model behind the agent is one, so no take's bytes, file name,
// URL or file route ever enters a tool answer. Free text is scrubbed before it is sent, and
// every answer is checked once more on the way out.

const ADDRESS = /\b(?:https?|wss?|blob|data|file):\S*/gi;
const ROUTE = /\/api\/\S*/g;
const MEDIA_FILE = /\S+\.(?:wav|mp3|ogg|opus|flac|m4a|aac|pcm|png|jpe?g|webp|gif|glb|gltf)\b/gi;
const BLOB = /[A-Za-z0-9+/]{120,}={0,2}/;

/** The answer sent instead of one that would have carried media or an address. */
export const WITHHELD = 'This answer was withheld: it would have carried media or an address, which the voice director never passes on.';

/** Free text with addresses, file routes and media file names replaced by plain words. */
export function plain(text: string | null | undefined): string {
  return (text ?? '')
    .replace(ADDRESS, '[address withheld]')
    .replace(ROUTE, '[address withheld]')
    .replace(MEDIA_FILE, '[file name withheld]');
}

/** True when the text carries media bytes, a data URI, a URL, a file route or a media file name. */
export function carriesMediaOrAddress(text: string): boolean {
  return new RegExp(ADDRESS.source, 'i').test(text) || new RegExp(ROUTE.source).test(text)
    || new RegExp(MEDIA_FILE.source, 'i').test(text) || BLOB.test(text);
}

/** One tool answer as the text the agent reads; withheld whole when it would leak. */
export function answer(value: unknown): string {
  const text = JSON.stringify(value);
  return carriesMediaOrAddress(text) ? JSON.stringify({error: WITHHELD}) : text;
}
