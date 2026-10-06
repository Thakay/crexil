// One attempt without material, drawn as its face: one line of what happened, one of
// what it cost, its next moves as buttons, free first, raw engine text behind DETAILS
// (laws 3, 4, 5). A move that sends work that reserves money wears money gold with its
// price on its face, kept whole when the label wraps, never the primary frame (law 6).
// attempts.ts decides every word; this only draws them inside the cell's frozen
// footprint (law 8), so a failure changes the cell's contents, never its size.
import type { AttemptFaceVM, RemedyId } from './attempts';
import s from './Filmstrip.module.css';

export function AttemptFace({face, onRemedy, busy = false}: {
  face: AttemptFaceVM; onRemedy: (id: RemedyId) => void; busy?: boolean;
}) {
  const tone = face.tone === 'dead' ? s.attemptDead : face.tone === 'waiting' ? s.attemptWaiting : s.attemptInfo;
  return <div role="group" aria-label={`Attempt ${face.label} · ${face.title.toLowerCase()}`}
    className={`${s.attemptFace} ${tone} ${face.remedies.length > 2 ? s.attemptCrowded : ''}`}>
    <div className={s.attemptHead}><strong>{face.title}</strong><span>{face.label} · {face.engine}</span></div>
    {face.cause && <p className={s.attemptCause} title={face.cause}>{face.cause}</p>}
    {face.retry && <p className={s.attemptRetry} title={face.retry.why}>{face.retry.label}</p>}
    {face.progress && <p className={s.attemptProgress} title={face.progress}>{face.progress}</p>}
    {face.cost && <p className={s.attemptLine} title={face.cost}>{face.cost}</p>}
    {face.remedies.length > 0 && <div className={`${s.attemptRemedies} ${face.detail || face.receipt ? s.besideDetails : ''}`}>{face.remedies.map(remedy =>
      <button key={remedy.id} className={remedy.primary ? s.remedyPrimary : remedy.price ? s.remedyPaid : undefined}
        aria-disabled={busy || undefined} title={remedy.title ?? remedy.label} onClick={() => {if (!busy) onRemedy(remedy.id);}}>
        {remedy.label}{remedy.price && <> · <span className={s.remedyPrice}>{remedy.price}</span></>}</button>)}</div>}
    {(face.detail || face.receipt) && <details className={s.attemptDetails}><summary>DETAILS</summary>
      {face.receipt && <pre>{face.receipt}</pre>}{face.detail && <pre>{face.detail}</pre>}</details>}
  </div>;
}
