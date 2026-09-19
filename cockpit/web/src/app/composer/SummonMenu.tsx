// Summon at cursor: the payload picker.
// `N` or right-click opens this palette where the pointer is; picking
// "Image · new root" arms the composer with that canvas point as the turn
// anchor — the node is born where the palette was. Entries that need a
// parent are listed and say so, never hidden (law 12).
//
// `Image · from a parent` arms the same composer with the SELECTED image
// node as the run's parent, so it needs exactly one image selected and
// says which take it would read. `3D · from an image` opens the To-3D
// plinth on that same parent (mesh/MeshPlinth) rather than arming the
// composer: a mesh needs an engine and a take count and nothing else, so
// it has its own surface. It still needs exactly one image, and says so
// when it hasn't got one. Truth-first list: parked payloads
// (video/texture/moodboard) are not invented into it.
//
// Where the take is BORN differs between the two, and that is why the mesh
// entry ignores the palette's point: an image root is born where the
// palette was, a mesh beside its source (mesh.ts `meshAnchor`), because it
// is a child of a card already on screen.
//
// Split for the repo's test pattern: SummonPalette is pure (props +
// store, vitest); SummonMenu resolves the eligible parent from the node
// feed and mounts the palette in a radix Popover at a fixed cursor anchor
// (portal, dismiss, collision-aware placement) — proven in e2e.
import { Command } from "cmdk";
import { Popover } from "radix-ui";
import { $api } from "../../api/client";
import { useUi } from "../state/ui";
import { targetLabel } from "../verbs/verbs";
import s from "./Composer.module.css";

type SummonEntry = {
  id: string;
  label: string;
  note: string; // right-hand mono annotation — curated static text
  live: boolean;
};

/** The one selected image node, when the selection is exactly that. */
export type Parent = { id: string; label: string };

function entries(parent: Parent | null): SummonEntry[] {
  return [
    { id: "image-root", label: "Image · new root", note: "↩", live: true },
    {
      id: "image-child",
      label: "Image · from a parent",
      note: parent === null ? "NEEDS 1 PARENT" : `FROM ${parent.label}`,
      live: parent !== null,
    },
    {
      id: "mesh",
      label: "3D · from an image",
      note: parent === null ? "NEEDS 1 PARENT" : `FROM ${parent.label}`,
      live: parent !== null,
    },
  ];
}

export function SummonPalette({ parent = null }: { parent?: Parent | null }) {
  const at = useUi((st) => st.summonAt);
  const arm = useUi((st) => st.arm);
  const openMesh = useUi((st) => st.openMesh);
  const closeSummon = useUi((st) => st.closeSummon);
  if (at === null) return null;
  return (
    <div className={s.summon}>
      <div className={`mono-label ${s.summonHead}`}>
        <span>N · Summon at cursor</span>
        <span className={s.esc}>Esc</span>
      </div>
      <Command label="Summon">
        <div className={s.summonSearch}>
          {/* eslint-disable-next-line jsx-a11y/no-autofocus */}
          <Command.Input autoFocus placeholder="Summon…" />
        </div>
        <Command.List className={s.summonList}>
          <Command.Empty className={s.summonEmpty}>
            Nothing answers to that here.
          </Command.Empty>
          {entries(parent).map((e) => (
            <Command.Item
              key={e.id}
              value={e.label}
              disabled={!e.live}
              onSelect={() => {
                // The mesh entry opens its own surface; the image entries
                // arm the composer at the palette's point.
                if (e.id === "mesh") {
                  if (parent !== null) openMesh(parent.id);
                  closeSummon();
                  return;
                }
                arm({
                  fx: at.fx,
                  fy: at.fy,
                  parents:
                    e.id === "image-child" && parent !== null
                      ? [parent.id]
                      : [],
                });
              }}
              className={s.item}
            >
              <span>{e.label}</span>
              <span className={s.note}>{e.note}</span>
            </Command.Item>
          ))}
        </Command.List>
      </Command>
      <p className={s.summonFoot}>
        Born where the palette opens. Entries needing a parent are listed
        and say so — select one on the canvas to arm them.
      </p>
    </div>
  );
}

export function SummonMenu({ workspaceId }: { workspaceId: string }) {
  const at = useUi((st) => st.summonAt);
  const selection = useUi((st) => st.selection);
  const closeSummon = useUi((st) => st.closeSummon);
  const nodesQ = $api.useQuery("get", "/api/workspaces/{wid}/nodes", {
    params: { path: { wid: workspaceId } },
  });
  // Exactly one selected IMAGE node is a source; a mesh or a pair is not,
  // and the row says which by staying on NEEDS 1 PARENT rather than going
  // live and failing at Run (law 12: never shown as capable when it isn't).
  const only =
    selection.length === 1
      ? (nodesQ.data ?? []).find((n) => n.id === selection[0])
      : undefined;
  const parent =
    only !== undefined && only.payload === "image"
      ? { id: only.id, label: targetLabel([only]) }
      : null;
  if (at === null) return null;
  return (
    <Popover.Root open onOpenChange={(o) => !o && closeSummon()}>
      <Popover.Anchor
        style={{ position: "fixed", left: at.sx, top: at.sy }}
      />
      <Popover.Portal>
        {/* Picking an entry arms the composer, which takes the caret; radix
            would hand focus back to whatever had it before the palette
            (the canvas pane, a chip) a tick later and steal it again. */}
        <Popover.Content
          side="bottom"
          align="start"
          sideOffset={2}
          onCloseAutoFocus={(e) => e.preventDefault()}
          asChild
        >
          <div>
            <SummonPalette parent={parent} />
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
