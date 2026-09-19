// The promote plinth.
//
// It opens in place under the verb row's hairline rather than over the
// canvas: promote is a verb on the selected take, so it belongs where the
// verbs are, and law 2 leaves no room for a modal anyway. Everything the
// hold would do is on screen before it — the folder, every file by its
// final path, what the take cost, whether it can be reproduced — because
// this control is, by design, a spend / manifest summary spelled out
// before the hold.
//
// When the destination is broken (failure case D5) the hold is not
// offered at all: the two halves of the server's refusal stand where it
// was, and the re-point control sits under the sentence that asks for
// one. Nothing here guesses; the folder is validated server-side before
// it is remembered, and again immediately before any write.
//
// The same control is reachable on a HEALTHY target, folded behind CHANGE
// on the destination column. Steering where an asset lands is a thing the
// artist may simply want to do — tying the only path editor to a failure
// would mean the cockpit could be pointed somewhere else only by breaking
// it first.
//
// The control is two doors onto one validated re-point. The browser is the
// first: it walks the drive one level at a time, every row a handle the
// server minted inside the fence, and POINT THE COCKPIT HERE posts that
// handle back — the artist never composes a path. The typed field is the
// second, for the artist who already knows the folder; both spellings
// work and both doors are refused in the same words.
import { useEffect, useState } from "react";
import { keepPreviousData, useQueryClient } from "@tanstack/react-query";
import { $api } from "../../api/client";
import { invalidateWorkspace } from "../invalidate";
import { useUi } from "../state/ui";
import { HoldButton } from "../tray/HoldButton";
import {
  IRREVERSIBLE,
  POINT_HERE,
  alreadyLine,
  brokenLines,
  browseHead,
  emptyLine,
  fenceLine,
  fileCount,
  folderWhy,
  holdLabel,
  promoteErr,
  startAt,
  targetLines,
  type Target,
} from "./promote";
import s from "./Promote.module.css";

function Browser({
  target,
  busy,
  onPick,
}: {
  target: Target;
  busy: boolean;
  onPick: (path: string) => void;
}) {
  // `""` is the top — the drive(s) the fence allows. The level in view is
  // the only state: what is in it is the server's answer, never remembered.
  const [at, setAt] = useState(() => startAt(target));
  const q = $api.useQuery(
    "get",
    "/api/promote-browse",
    { params: { query: { path: at } } },
    // the previous level stays on screen while the next one loads — a list
    // that blanks between clicks reads as a folder that emptied
    { placeholderData: keepPreviousData },
  );
  const level = q.data;
  const here = level?.here ?? null;

  return (
    <div className={s.browser} role="group" aria-label="Browse the drive">
      <div className={s.browserHead}>
        <button
          type="button"
          className={`mono-micro ${s.up}`}
          disabled={level === undefined || level.parent === null}
          onClick={() => {
            if (level?.parent != null) setAt(level.parent);
          }}
        >
          UP
        </button>
        <span className={s.browserPath}>
          {level === undefined ? "Looking…" : browseHead(level, target)}
        </span>
        {here !== null && (
          <button
            type="button"
            className={`mono-label ${s.pick}`}
            disabled={!here.ok || busy}
            onClick={() => onPick(here.path)}
          >
            {POINT_HERE}
          </button>
        )}
      </div>
      {/* the folder in view can't take a promotion: said here, above its
          list, so stepping around it is still possible and the button's
          refusal has its reason beside it */}
      {here !== null && !here.ok && (
        <p className={s.note}>{folderWhy(here)}</p>
      )}
      {q.isError && (
        <p className={s.err}>
          {promoteErr(q.error).detail}{" "}
          <button
            type="button"
            className={`mono-micro ${s.change}`}
            onClick={() => setAt("")}
          >
            BACK TO THE DRIVE
          </button>
        </p>
      )}
      {level !== undefined && (
        <>
          <ul className={s.folderList}>
            {level.folders.map((f) => {
              const why = folderWhy(f);
              return (
                <li key={f.path}>
                  <button
                    type="button"
                    className={`${s.folder} ${why === null ? "" : s.folderUnfit}`}
                    onClick={() => setAt(f.path)}
                  >
                    <span className={s.folderName}>{f.name}</span>
                    {why !== null && <span className={s.folderWhy}>{why}</span>}
                    <span className={s.folderStep} aria-hidden="true">
                      ›
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          {emptyLine(level) !== null && (
            <p className={s.note}>{emptyLine(level)}</p>
          )}
        </>
      )}
    </div>
  );
}

function Repoint({
  target,
  onDone,
}: {
  target: Target;
  onDone?: () => void;
}) {
  const [path, setPath] = useState("");
  const queryClient = useQueryClient();
  const mut = $api.useMutation("post", "/api/promote-target", {
    onSuccess: () => {
      setPath("");
      void queryClient.invalidateQueries();
      onDone?.();
    },
  });
  const err = mut.isError ? promoteErr(mut.error) : null;

  return (
    <div className={s.repoint}>
      <span className={`mono-micro ${s.repointHead}`}>
        POINT THE COCKPIT AT ANOTHER FOLDER
      </span>
      <Browser
        target={target}
        busy={mut.isPending}
        onPick={(p) => mut.mutate({ body: { path: p } })}
      />
      <label className={`mono-micro ${s.repointHead}`} htmlFor="promote-root">
        OR TYPE ITS PATH
      </label>
      <div className={s.repointRow}>
        <input
          id="promote-root"
          className={s.repointInput}
          value={path}
          // The artist's spelling is what the field invites — the
          // server takes either and translates at the edge.
          placeholder={target.windows_path ?? target.default_path}
          spellCheck={false}
          onChange={(e) => setPath(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && path.trim()) mut.mutate({ body: { path } });
          }}
        />
        <button
          type="button"
          className={`mono-label ${s.repointGo}`}
          disabled={!path.trim() || mut.isPending}
          onClick={() => mut.mutate({ body: { path } })}
        >
          USE THIS FOLDER
        </button>
        {/* Nothing is offered that isn't there: the reset only appears once
            something has actually been pointed here. */}
        {target.source === "set-here" && (
          <button
            type="button"
            className={`mono-label ${s.repointReset}`}
            onClick={() => mut.mutate({ body: { path: "" } })}
          >
            BACK TO .ENV
          </button>
        )}
      </div>
      <p className={s.note}>{fenceLine(target)}</p>
      {err !== null && (
        <p className={s.err}>
          {err.detail}
          {err.remedy !== undefined && <span> {err.remedy}</span>}
        </p>
      )}
    </div>
  );
}

export function PromotePlinth({
  nodeId,
  workspaceId,
}: {
  nodeId: string;
  workspaceId: string;
}) {
  const closePromote = useUi((st) => st.closePromote);
  const queryClient = useQueryClient();
  const [done, setDone] = useState<string | null>(null);

  const previewQ = $api.useQuery(
    "get",
    "/api/nodes/{nid}/promote-preview",
    { params: { path: { nid: nodeId } } },
  );
  const mut = $api.useMutation("post", "/api/nodes/{nid}/promote", {
    onSuccess: (r) => {
      setDone(r.promoted_path);
      void invalidateWorkspace(queryClient, workspaceId);
      void previewQ.refetch();
    },
  });

  // Esc closes the plinth without spending anything — the same gesture the
  // composer and the region surface answer to.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closePromote();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [closePromote]);

  const [repointing, setRepointing] = useState(false);
  const preview = previewQ.data;
  const target = preview?.target;
  const err = mut.isError ? promoteErr(mut.error) : null;

  return (
    <section className={s.plinth} aria-label="Promote">
      <div className={s.head}>
        <span className={`mono-label ${s.title}`}>PROMOTE</span>
        <span className={`mono-micro ${s.facts}`}>{preview?.facts ?? ""}</span>
        <span className={s.spacer} />
        <button
          type="button"
          className={`mono-micro ${s.close}`}
          onClick={closePromote}
        >
          ESC CLOSES · NOTHING LEAVES
        </button>
      </div>

      {previewQ.isPending && (
        <p className={s.note}>Reading what this would write…</p>
      )}
      {previewQ.isError && (
        <p className={s.err}>{promoteErr(previewQ.error).detail}</p>
      )}

      {preview !== undefined && target !== undefined && (
        <>
          <div className={s.cols}>
            <div className={s.col}>
              <span className={`mono-micro ${s.colHead}`}>
                WHERE IT GOES
                {target.ok && (
                  <button
                    type="button"
                    className={`mono-micro ${s.change}`}
                    onClick={() => setRepointing((v) => !v)}
                  >
                    {repointing ? "KEEP THIS ONE" : "CHANGE"}
                  </button>
                )}
              </span>
              {targetLines(target).map((line) => (
                <p key={line} className={s.path}>
                  {line}
                </p>
              ))}
              {target.ok && repointing && (
                <Repoint target={target} onDone={() => setRepointing(false)} />
              )}
            </div>
            <div className={s.col}>
              <span className={`mono-micro ${s.colHead}`}>
                {fileCount(preview)}
              </span>
              <ul className={s.files}>
                {preview.files.map((f) => (
                  <li key={f.path} className={s.file}>
                    <span className={s.filePath}>{f.path}</span>
                    <span className={s.fileWhat}>{f.what}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>

          {/* Law 5: money is always stated, including when the answer is
              "nothing". The replay tier sits beside it — an external take
              is a record, not a recipe, and the manifest says so. */}
          <p className={s.cost}>{preview.cost}</p>
          <p className={s.note}>{preview.replayable}</p>
          {alreadyLine(preview) !== null && (
            <p className={s.note}>{alreadyLine(preview)}</p>
          )}

          <div className={s.rule} />

          {target.ok ? (
            <>
              <p className={s.irreversible}>{IRREVERSIBLE}</p>
              <HoldButton
                label={holdLabel(preview)}
                disabled={mut.isPending}
                onHold={() =>
                  mut.mutate({ params: { path: { nid: nodeId } } })
                }
              />
            </>
          ) : (
            <div className={s.broken}>
              {brokenLines(target).map((line) => (
                <p key={line} className={s.brokenLine}>
                  {line}
                </p>
              ))}
              <Repoint target={target} />
            </div>
          )}

          {err !== null && (
            <div className={s.broken}>
              <p className={s.brokenLine}>{err.detail}</p>
              {/* A hold that hit a broken target gets the same control as a
                  plinth that opened onto one — the refusal is the same D5. */}
              {err.remedy !== undefined && <Repoint target={target} />}
            </div>
          )}
          {done !== null && (
            <p className={s.done}>
              <span className="mono-label">PROMOTED</span> {done}
            </p>
          )}
        </>
      )}
    </section>
  );
}
