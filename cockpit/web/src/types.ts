// Wire types: aliases over the generated OpenAPI schema. The
// server's Pydantic models are the single source of truth. Regenerate with
// scripts/gen-api-types.sh; never hand-edit src/generated/openapi.d.ts.
// A tsc failure in a component after regenerating IS the drift detector:
// the UI reads a field the server no longer states — fix the model, not
// the alias.
//
// Names are the server's, verbatim. Two of them, `Node` and `Event`, shadow
// the DOM globals inside a module that imports them; reach for
// `globalThis.Node` / `globalThis.Event` if a file ever needs both. React
// Flow's own `Node` is imported as `FlowNode` where the two meet
// (canvas/graph.ts).
//
// Only names a component imports are aliased (knip flags the rest);
// add a line when a reader appears.
import type { components } from "./generated/openapi";

// ── app-level: the preset catalogue and the external bench ────────────────

export type InputSpec = components["schemas"]["InputSpec"];
export type Preset = components["schemas"]["Preset"];
export type ProviderModel = components["schemas"]["ProviderModel"];

// ── the product model: workspaces · turns · slots · nodes ─────────────────

export type WorkspaceRow = components["schemas"]["WorkspaceRow"];
export type WorkspaceDetail = components["schemas"]["WorkspaceDetail"];
export type Turn = components["schemas"]["Turn"];
export type Slot = components["schemas"]["Slot"];
export type SlotStatus = components["schemas"]["SlotStatus"];
export type SlotView = components["schemas"]["SlotView"];
export type BillCheck = components["schemas"]["BillCheck"];
// B2's countdown, C2's card state, E6's headroom sheet
export type RetryWait = components["schemas"]["RetryWait"];
export type Headroom = components["schemas"]["Headroom"];
export type Passes = components["schemas"]["Passes"];
export type QueueView = components["schemas"]["QueueView"];
export type Bench = components["schemas"]["Bench"];
export type Event = components["schemas"]["Event"];
export type Node = components["schemas"]["Node"];
export type TrayNode = components["schemas"]["TrayNode"];
export type BackfillRefusal = components["schemas"]["BackfillRefusal"];
// the authored tables served as-is: configs/dials.json + engines.json
export type Dials = components["schemas"]["Dials"];
export type Dial = components["schemas"]["Dial"];
export type DialEngine = components["schemas"]["DialEngine"];
export type Engines = components["schemas"]["Engines"];
export type Engine = components["schemas"]["Engine"];
export type EngineCapability = components["schemas"]["EngineCapability"];
// whether the 3D lane's door is open: the To-3D roster's
// dashed-0 reason comes off the wire worded (law 12).
export type MeshHealth = components["schemas"]["MeshHealth"];
