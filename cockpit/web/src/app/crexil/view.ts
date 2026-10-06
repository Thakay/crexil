// The project room's view state: which room is open, which layers lie over it, what
// is being acted on and which preparation is in the drawer. Keys, buttons and agent
// tools change it through these actions only. Escape closes the top layer in one
// fixed order (closeTop). Server-owned state never lives here.
import { createContext, useContext } from 'react';
import { createStore, useStore, type StoreApi } from 'zustand';
import type { RecordScope } from './RecordRoom';

/** A room that replaces the canvas. At most one is open. */
export type Panel = {kind: 'reuse'} | {kind: 'backup'} | {kind: 'delivery'; ids: string[]}
  | {kind: 'returned'; ids: string[]} | {kind: 'sequences'};

/** A layer over the canvas or a room. Escape closes them top first: group, inspect, picker, project edit.
 *  The picker names its direction by id; the room reads the live direction while takes land. */
export type Overlay = {kind: 'group'; id: string} | {kind: 'inspect'; id: string}
  | {kind: 'picker'; direction: string; left: number; top: number} | {kind: 'projectEdit'};
const OVERLAY_ORDER: Overlay['kind'][] = ['group', 'inspect', 'picker', 'projectEdit'];

export type View = {
  /** The Filmstrip's scope: a row's grid room, the tray, or the whole project (null). */
  room: number | 'tray' | null;
  /** The sequence a grid room returns to when it was opened from Sequences. */
  sequenceReturn: string | null;
  panel: Panel | null;
  /** The project record, and the material to inspect again when it closes. */
  record: {scope: RecordScope; returnNode: string | null} | null;
  /** A material's own record, opened from inspection inside the record. */
  nestedRecord: string | null;
  /** The record stays open beneath the grid room or tray it opened. */
  recordDestination: boolean;
  overlays: Overlay[];
  acting: string[];
  drawer: string | null;
};

/** Where focus returns after a layer closes. */
export type FocusReturn = 'origin' | 'record' | 'nestedRecord' | 'none';

export const initialView = (start: Partial<View> = {}): View => ({
  room: null, sequenceReturn: null, panel: null, record: null, nestedRecord: null, recordDestination: false,
  overlays: [], acting: [], drawer: null, ...start,
});

export const overlay = <K extends Overlay['kind']>(view: View, kind: K) =>
  view.overlays.find((layer): layer is Extract<Overlay, {kind: K}> => layer.kind === kind);
const without = (view: View, kind: Overlay['kind']) => view.overlays.filter(layer => layer.kind !== kind);
const withOverlay = (view: View, layer: Overlay) => [...without(view, layer.kind), layer];
/** Opening a room that replaces the canvas closes the record beneath it. */
const panelOpen = (panel: Panel): Partial<View> => ({panel, record: null, recordDestination: false, nestedRecord: null});

/** Whether anything lies over the Filmstrip or its grid room: the drawer, any overlay, a
 *  room that replaces the canvas, or the record (unless it waits beneath a grid room it
 *  opened). Plain keys act on the Filmstrip only while nothing covers it. */
export const covered = (view: View): boolean => Boolean(view.drawer || view.overlays.length || view.panel
  || ((view.record || view.nestedRecord) && !view.recordDestination));

/** Close the top layer. The order is fixed: overlays, a room over the canvas, the record
 * and its destinations, the acting set, then a grid room or the tray. */
export function closeTop(view: View): {view: View; focus: FocusReturn} {
  const recordFocus: FocusReturn = view.nestedRecord ? 'nestedRecord' : 'record';
  const top = OVERLAY_ORDER.find(kind => overlay(view, kind));
  if (top) {
    const next = {...view, overlays: without(view, top)};
    return {view: next, focus: top === 'inspect' && view.record ? recordFocus : 'origin'};
  }
  if (view.panel) return {view: {...view, panel: null}, focus: 'origin'};
  if (view.recordDestination) return {view: {...view, recordDestination: false, room: null}, focus: recordFocus};
  if (view.nestedRecord) {
    return {view: {...view, nestedRecord: null, overlays: withOverlay(view, {kind: 'inspect', id: view.nestedRecord})}, focus: 'none'};
  }
  if (view.record) {
    const back = view.record.returnNode;
    return {view: {...view, record: null, overlays: back ? withOverlay(view, {kind: 'inspect', id: back}) : view.overlays}, focus: 'origin'};
  }
  if (view.acting.length) return {view: {...view, acting: []}, focus: 'origin'};
  if (view.room !== null) {
    return {view: {...view, room: null, ...(view.sequenceReturn ? panelOpen({kind: 'sequences'}) : {})}, focus: 'origin'};
  }
  return {view, focus: 'origin'};
}

/** The project record. From inspection inside the record it opens the material's own record. */
export function openRecord(view: View, scope: RecordScope = {}, returnNode: string | null = null): View {
  if (returnNode && view.record) return {...view, nestedRecord: returnNode, overlays: without(view, 'inspect')};
  return {...view, nestedRecord: null, record: {scope, returnNode}, recordDestination: false, room: null, acting: [], panel: null,
    overlays: view.overlays.filter(layer => layer.kind !== 'inspect' && layer.kind !== 'picker')};
}

export type ViewActions = {
  closeTop: () => FocusReturn;
  act: (ids: string[]) => void;
  clearActing: () => void;
  openDrawer: (id: string) => void;
  closeDrawer: () => void;
  /** A new preparation opens in the drawer and the acting set and picker close. */
  prepared: (id: string) => void;
  inspect: (id: string) => void;
  openGroup: (id: string) => void;
  closeGroup: () => void;
  openPicker: (direction: string, left: number, top: number) => void;
  toggleProjectEdit: () => void;
  openProjectEdit: () => void;
  closeProjectEdit: () => void;
  openRecord: (scope?: RecordScope, returnNode?: string | null) => void;
  /** Leave the record for a grid room or the tray; Escape returns to the record. */
  showFromRecord: (room: number | 'tray') => void;
  openGrid: (row: number) => void;
  toggleTray: () => void;
  /** The room head's Esc button: leave the grid room or tray for where it was opened from. */
  leaveRoom: () => FocusReturn;
  showImport: () => void;
  /** Material arrived through the import room; the room closes. */
  closeImport: () => void;
  showSequences: () => void;
  showSequence: (id: string) => void;
  /** A run opened from Sequences: its grid room returns to that sequence. */
  showRunFromSequence: (row: number, sequenceId: string) => void;
  showDeliveries: () => void;
  showBackup: () => void;
  deliverActing: () => void;
  returnActing: () => void;
  /** Inspect material from a room that replaces the canvas, closing that room. */
  inspectFromPanel: (id: string) => void;
};

export type ViewStore = View & ViewActions;

export function createViewStore(start: Partial<View> = {}): StoreApi<ViewStore> {
  return createStore<ViewStore>()((set, get) => {
    const change = (next: (view: View) => Partial<View>) => set(state => next(state));
    const close = () => {
      const {view, focus} = closeTop(get());
      set(view);
      return focus;
    };
    return {
      ...initialView(start),
      closeTop: close,
      act: ids => change(() => ({acting: ids})),
      clearActing: () => change(() => ({acting: []})),
      openDrawer: id => change(() => ({drawer: id})),
      closeDrawer: () => change(() => ({drawer: null})),
      prepared: id => change(view => ({drawer: id, acting: [], overlays: without(view, 'picker')})),
      inspect: id => change(view => ({overlays: withOverlay(view, {kind: 'inspect', id})})),
      openGroup: id => change(view => ({overlays: withOverlay(view, {kind: 'group', id})})),
      closeGroup: () => change(view => ({overlays: without(view, 'group')})),
      openPicker: (direction, left, top) => change(view => ({overlays: withOverlay(view, {kind: 'picker', direction, left, top})})),
      toggleProjectEdit: () => change(view => ({overlays: overlay(view, 'projectEdit') ? without(view, 'projectEdit') : withOverlay(view, {kind: 'projectEdit'})})),
      openProjectEdit: () => change(view => ({overlays: withOverlay(view, {kind: 'projectEdit'})})),
      closeProjectEdit: () => change(view => ({overlays: without(view, 'projectEdit')})),
      openRecord: (scope, returnNode) => change(view => openRecord(view, scope, returnNode)),
      showFromRecord: room => change(() => ({recordDestination: true, room})),
      openGrid: row => change(() => ({sequenceReturn: null, room: row})),
      toggleTray: () => change(view => ({panel: null, record: null, recordDestination: false, nestedRecord: null, sequenceReturn: null,
        room: view.room === 'tray' ? null : 'tray'})),
      leaveRoom: () => {
        set({acting: []});
        if (get().recordDestination) return close();
        change(view => ({room: null, ...(view.sequenceReturn ? panelOpen({kind: 'sequences'}) : {})}));
        return 'none';
      },
      showImport: () => change(() => ({acting: [], room: null, ...panelOpen({kind: 'reuse'})})),
      closeImport: () => change(view => (view.panel?.kind === 'reuse' ? {panel: null} : {})),
      showSequences: () => change(() => ({acting: [], room: null, ...panelOpen({kind: 'sequences'})})),
      showSequence: id => change(() => ({sequenceReturn: id, room: null, ...panelOpen({kind: 'sequences'})})),
      showRunFromSequence: (row, sequenceId) => change(() => ({sequenceReturn: sequenceId, panel: null, room: row})),
      showDeliveries: () => change(() => ({acting: [], ...panelOpen({kind: 'delivery', ids: []})})),
      showBackup: () => change(() => ({acting: [], room: null, ...panelOpen({kind: 'backup'})})),
      deliverActing: () => change(view => ({acting: [], ...panelOpen({kind: 'delivery', ids: [...view.acting]})})),
      returnActing: () => change(view => ({acting: [], ...panelOpen({kind: 'returned', ids: [...view.acting]})})),
      inspectFromPanel: id => change(view => ({panel: null, overlays: withOverlay(view, {kind: 'inspect', id})})),
    };
  });
}

export const ViewContext = createContext<StoreApi<ViewStore> | null>(null);

/** The project room's view store; only components inside a project room may call this. */
export function useViewStore(): StoreApi<ViewStore> {
  const store = useContext(ViewContext);
  if (!store) throw new Error('The project view is only available inside a project room.');
  return store;
}

export const useView = <T>(select: (state: ViewStore) => T): T => useStore(useViewStore(), select);
