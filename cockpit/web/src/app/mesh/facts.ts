// A mesh's facts line — the one sentence of numbers every surface showing a
// mesh prints (the lightbox and the cell, the decimate plinth's
// parent). It lives here rather than in the lightbox because it is a fact
// about the MESH, and two surfaces printing the same numbers differently
// would be two answers to one question (one home per fact).
import type { Node } from "../../types";
import { decimateOutcome } from "./decimate";

/** The mesh's own facts, in the mono register the strip and the cell use.
 *  Every value is funnel.py's measurement — a fact the record doesn't carry
 *  is left out rather than guessed (law 12), so a sparse record reads short
 *  instead of reading wrong. */
export function meshFactsLine(node: Node): string {
  const m = node.mesh;
  if (!m) return "";
  const n = (v: number) => v.toLocaleString("en-US");
  const parts: string[] = [];
  if (m.engine) parts.push(m.engine.toUpperCase());
  if (m.triangles != null) parts.push(`${n(m.triangles)} TRIS`);
  if (m.vertices != null) parts.push(`${n(m.vertices)} VERTS`);
  if (m.textured != null) parts.push(m.textured ? "TEXTURED" : "UNTEXTURED");
  // open surfaces are what TRELLIS.2 legitimately makes — stated, not judged
  if (m.watertight != null)
    parts.push(m.watertight ? "WATERTIGHT" : "OPEN SURFACES");
  if (m.file_mb != null) parts.push(`${m.file_mb} MB`);
  // A decimated child says what was ASKED beside what it reached.
  // The count above is the artefact's truth; this is the request's — and on
  // a machine UV atlas they differ by design, so a line carrying only the
  // first would be honest about the mesh and silent about the refusal.
  const asked = decimateOutcome(m);
  if (asked !== null) parts.push(asked);
  return parts.join(" · ");
}
