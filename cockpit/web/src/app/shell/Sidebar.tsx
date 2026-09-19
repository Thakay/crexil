// Workspace navigation and management. The structural sidebar of the
// shell: a collapsible left sidebar for navigation between workspaces,
// collapsed from the top bar (mod+\, Shell.tsx). There is no home screen:
// this list IS the surface where a workspace is found, named, renamed and
// read at a glance, so the node and promoted counts ride the row here.
//
// Order is most-recently-touched first, straight from the server — every
// verb touches its workspace, so the list is the artist's own trail back.
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { $api } from "../../api/client";
import type { WorkspaceRow } from "../../types";
import { invalidateWorkspace } from "../invalidate";
import { href } from "../routes";
import styles from "./Sidebar.module.css";

// The filter is a client-side pass over the list already in hand: this is a
// solo artist's handful of assets (the store has no workspace search;
// FTS stays for nodes), so a round-trip per keystroke
// would buy nothing. It appears only once the list outgrows the panel, because a
// search box over four rows is furniture, not a control.
const FILTER_APPEARS_AT = 7;

function matches(w: WorkspaceRow, q: string): boolean {
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  return (
    w.name.toLowerCase().includes(needle) ||
    w.slug.toLowerCase().includes(needle)
  );
}

// What the row says about itself, in words rather than a bare pair of
// numbers. Promotion is the asset leaving for the tree, so a workspace that
// has never promoted says nothing about it — a "0 promoted" would be noise
// on every row that hasn't got there yet, and the green half only appears
// where it means something (the promoted chip, at list scale).
function tally(w: WorkspaceRow): string {
  if (w.nodes === 0) return "empty";
  return `${w.nodes} node${w.nodes === 1 ? "" : "s"}`;
}

function CreateForm() {
  const queryClient = useQueryClient();
  const create = $api.useMutation("post", "/api/workspaces", {
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: $api.queryOptions("get", "/api/workspaces").queryKey,
      }),
  });
  const [name, setName] = useState("");

  return (
    <form
      className={styles.create}
      onSubmit={(e) => {
        e.preventDefault();
        const trimmed = name.trim();
        if (!trimmed || create.isPending) return;
        create.mutate(
          { body: { name: trimmed } },
          { onSuccess: () => setName("") },
        );
      }}
    >
      <label className="mono-micro" htmlFor="workspace-name">
        New workspace
      </label>
      <div className={styles.createRow}>
        <input
          id="workspace-name"
          className={styles.input}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="asset name"
        />
        <button
          type="submit"
          className={styles.button}
          disabled={!name.trim() || create.isPending}
        >
          Create
        </button>
      </div>
      {create.isError && (
        <p className="effect-line">
          The workspace wasn't created — the cockpit server didn't answer.
          Nothing was charged. Try again once it's running.
        </p>
      )}
    </form>
  );
}

// The rename sits on the OPEN workspace only. A per-row control would be
// hover-revealed at this width, and a hover-only affordance is not
// keyboard-operable; the open one is always reachable. The slug never
// moves — it is the promote folder, so the files already written under
// it stay found.
function RenameRow({ workspace }: { workspace: WorkspaceRow }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(workspace.name);
  const inputRef = useRef<HTMLInputElement>(null);
  const rename = $api.useMutation("patch", "/api/workspaces/{wid}", {
    onSuccess: () => {
      // the name rides the top bar (the detail query) as well as this row
      invalidateWorkspace(queryClient, workspace.id);
      setEditing(false);
    },
  });

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  // A rename elsewhere (or a different workspace opening) must not leave a
  // stale draft behind the button.
  useEffect(() => {
    setEditing(false);
    setDraft(workspace.name);
  }, [workspace.id, workspace.name]);

  if (!editing) {
    return (
      <button
        type="button"
        className={`mono-micro ${styles.rename}`}
        onClick={() => setEditing(true)}
      >
        Rename
      </button>
    );
  }

  return (
    <form
      className={styles.renameForm}
      onSubmit={(e) => {
        e.preventDefault();
        const trimmed = draft.trim();
        if (!trimmed || rename.isPending) return;
        if (trimmed === workspace.name) return setEditing(false);
        rename.mutate({
          params: { path: { wid: workspace.id } },
          body: { name: trimmed },
        });
      }}
    >
      <input
        ref={inputRef}
        className={styles.input}
        aria-label={`Rename ${workspace.name}`}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            setDraft(workspace.name);
            setEditing(false);
          }
          // the canvas listens for single keys (N, T) — a name being typed
          // is not a hotkey
          e.stopPropagation();
        }}
      />
      <div className={styles.renameRow}>
        <button
          type="submit"
          className={styles.button}
          disabled={!draft.trim() || rename.isPending}
        >
          Save
        </button>
        <button
          type="button"
          className={styles.button}
          onClick={() => {
            setDraft(workspace.name);
            setEditing(false);
          }}
        >
          Cancel
        </button>
      </div>
      {rename.isError && (
        <p className="effect-line">
          The name didn't change — the cockpit server didn't answer. Nothing
          else about the workspace was touched.
        </p>
      )}
    </form>
  );
}

export function Sidebar({ activeId }: { activeId: string | null }) {
  const list = $api.useQuery("get", "/api/workspaces");
  const [filter, setFilter] = useState("");

  const all = list.data ?? [];
  const shown = all.filter((w) => matches(w, filter));
  const active = all.find((w) => w.id === activeId);

  return (
    <nav className={styles.sidebar} aria-label="Workspaces">
      <p className="mono-label">Workspaces</p>

      <CreateForm />

      {all.length >= FILTER_APPEARS_AT && (
        <div className={styles.filter}>
          <label className="mono-micro" htmlFor="workspace-filter">
            Find
          </label>
          <input
            id="workspace-filter"
            className={styles.input}
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            onKeyDown={(e) => e.stopPropagation()}
            placeholder="name or slug"
          />
        </div>
      )}

      {list.isPending && <p className="mono-label">Loading</p>}
      {list.isError && (
        <p className="effect-line">
          Couldn't reach the cockpit server, so no workspaces are shown.
          Nothing was lost — start it and this list will fill in.
        </p>
      )}
      {list.data &&
        (all.length === 0 ? (
          <p className="effect-line">No workspaces yet. Name one above.</p>
        ) : shown.length === 0 ? (
          <p className="effect-line">
            No workspace matches "{filter.trim()}". All {all.length} are still
            there — clear the box to see them.
          </p>
        ) : (
          <ul className={styles.list}>
            {shown.map((w) => (
              <li key={w.id}>
                <a
                  className={styles.row}
                  href={href.workspace(w.id)}
                  aria-current={w.id === activeId ? "page" : undefined}
                >
                  <span className="mono-micro">{w.slug}</span>
                  <span className={styles.rowName}>{w.name}</span>
                  <span className={`mono-micro ${styles.tally}`}>
                    <span>{tally(w)}</span>
                    {w.promoted > 0 && (
                      <span className={styles.promoted}>
                        {w.promoted} promoted
                      </span>
                    )}
                  </span>
                </a>
                {w.id === activeId && active && (
                  <RenameRow workspace={active} />
                )}
              </li>
            ))}
          </ul>
        ))}
    </nav>
  );
}
