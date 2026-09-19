// The promote plinth's words.
// Pure — vitest exercises it with plain preview objects, no React.
//
// Where the files land, what the manifest holds and the replay tier are the
// server's. What this file owns is the register: the consequence stated
// plainly BEFORE the hold, one line each, no status codes.
//
// Every sentence here is curated static text; the server's own words —
// the target's message and remedy, the cost line, the replay tier — are
// passed through verbatim, never paraphrased.
import type { components } from "../../generated/openapi";

export type Preview = components["schemas"]["PromotePreview"];
export type Target = components["schemas"]["PromoteTarget"];

export const IRREVERSIBLE =
  "Promotion cannot be un-happened from here. The take stays on the canvas; " +
  "what leaves is a copy, and only the asset tree can take it back.";

/** The hold's face. Says PROMOTE once, and AGAIN when it isn't the first. */
export function holdLabel(preview: Pick<Preview, "already">): string {
  return preview.already.length === 0
    ? "HOLD TO PROMOTE"
    : "HOLD TO PROMOTE AGAIN";
}

/**
 * What an earlier promotion of this take means now — stated, never used to
 * refuse. The cockpit doesn't watch the asset tree, so it cannot know the
 * files are still there, and claiming otherwise would be a guess.
 */
export function alreadyLine(preview: Pick<Preview, "already">): string | null {
  const n = preview.already.length;
  if (n === 0) return null;
  const when = new Date(preview.already[n - 1]).toLocaleString();
  const times = n === 1 ? "once" : `${n} times`;
  return (
    `This take has already gone out ${times} — last on ${when}. ` +
    "Promoting again writes a second copy under a new id; the cockpit " +
    "doesn't watch the folder, so it can't tell you whether the first is " +
    "still there."
  );
}

/** The destination, and which disk it's on: the promotion boundary,
 *  never silent. */
/**
 * The destination, in the artist's spelling first.
 *
 * `D:\PROMOTED` is how a Windows user names the folder;
 * `/mnt/d/PROMOTED` is what a refusal, a manifest and a log will say.
 * Both are printed, the artist's leading. Off a Windows drive there is
 * only one spelling and it stands alone.
 */
export function targetLines(target: Target): string[] {
  const lines =
    target.windows_path === null
      ? [`${target.path} — ${target.where}`]
      : [`${target.windows_path} — ${target.where}`, `${target.path} in here`];
  if (target.source === "set-here")
    lines.push(
      `Pointed here from the cockpit; ${target.default_path} is what .env says.`,
    );
  return lines;
}

/** What the re-point control may reach at all — a fence you can't
 *  see is just a locked door, so the plinth prints it beside the field. */
export function fenceLine(target: Target): string {
  return `Anywhere on ${target.roots_said}. The folder has to be there already — the cockpit never creates one, so a typo can't build a look-alike tree on the wrong disk.`;
}

/** The blocked face's two halves (D5): what happened, then what to do. */
export function brokenLines(target: Target): string[] {
  if (target.ok) return [];
  return [target.message, target.remedy].filter(
    (s): s is string => typeof s === "string" && s.length > 0,
  );
}

/** The refusal a POST came back with, in the server's own words. */
export function promoteErr(e: unknown): { detail: string; remedy?: string } {
  const b = e as { detail?: unknown; remedy?: unknown } | null;
  const detail =
    typeof b?.detail === "string"
      ? b.detail
      : "That didn't go through — nothing was copied out and the take is untouched.";
  return typeof b?.remedy === "string"
    ? { detail, remedy: b.remedy }
    : { detail };
}

/** `n FILES` / `1 FILE` — the count above the list, in the row's grammar. */
export function fileCount(preview: Pick<Preview, "files">): string {
  const n = preview.files.length;
  return `${n} FILE${n === 1 ? "" : "S"}`;
}

// ── the picker ──────────────────────────────────────────────────────────
//
// A browser hands back a handle, never a path: every folder it shows is one
// the server enumerated inside the fence, and choosing one posts the
// server's own string back. The typed field stays beside it for the artist
// who knows the path — both go through the same validated re-point.

export type Browse = components["schemas"]["PromoteBrowse"];
export type Folder = components["schemas"]["PromoteFolder"];

/** The browser's own button — it points the cockpit, it never promotes. */
export const POINT_HERE = "POINT THE COCKPIT HERE";

/** Where the browser opens: where the cockpit points today when that folder
 *  is there, otherwise the top — the drive itself (the fence, enumerated),
 *  because a target that has gone away is exactly when the way back down
 *  from the drive is the thing to offer. `""` is the top's own name. */
export function startAt(target: Pick<Target, "ok" | "path">): string {
  return target.ok ? target.path : "";
}

/** The where-am-I line, in the artist's spelling; at the top, the drive(s)
 *  the fence allows — the same words the fence line uses. */
export function browseHead(
  level: Pick<Browse, "here">,
  target: Pick<Target, "roots_said">,
): string {
  if (level.here === null) return target.roots_said;
  return level.here.windows_path ?? level.here.path;
}

/** A folder that couldn't take a promotion says why, beside its name — shown
 *  de-emphasised, never dropped from the list (law 12). */
export function folderWhy(f: Pick<Folder, "ok" | "why">): string | null {
  if (f.ok) return null;
  return f.why ?? "can't take a promotion right now";
}

/** The line under an empty list: the server's reason when the folder could
 *  not be read, otherwise simply the end of the road. */
export function emptyLine(
  level: Pick<Browse, "folders" | "note">,
): string | null {
  if (level.folders.length > 0) return null;
  return level.note ?? "No folders inside this one.";
}
