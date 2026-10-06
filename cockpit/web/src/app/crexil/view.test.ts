import { describe, expect, it } from 'vitest';
import type { Direction } from './api';
import { closeTop, createViewStore, initialView, openRecord, overlay, type View } from './view';

const direction = {id: 'd1', revision: 3} as Direction;
const close = (view: View) => closeTop(view);

describe('closeTop', () => {
  it('closes overlays top first: files, inspection, picker, project edit', () => {
    let view = initialView({overlays: [{kind: 'projectEdit'}, {kind: 'picker', direction, left: 0, top: 0},
      {kind: 'inspect', id: 'n1'}, {kind: 'group', id: 'g1'}]});
    const order: string[] = [];
    while (view.overlays.length) {
      const before = view.overlays.map(layer => layer.kind);
      view = close(view).view;
      order.push(before.find(kind => !view.overlays.some(layer => layer.kind === kind))!);
    }
    expect(order).toEqual(['group', 'inspect', 'picker', 'projectEdit']);
  });
  it('returns focus to the record when inspection opened from the record closes', () => {
    const view = initialView({record: {scope: {}, returnNode: null}, overlays: [{kind: 'inspect', id: 'n1'}]});
    expect(close(view)).toMatchObject({focus: 'record'});
    expect(close({...view, nestedRecord: 'n0'}).focus).toBe('nestedRecord');
  });
  it('closes a room over the canvas before the record, the acting set or a grid room', () => {
    const view = initialView({panel: {kind: 'reuse'}, acting: ['n1'], room: 2});
    const next = close(view).view;
    expect(next.panel).toBeNull();
    expect(next.acting).toEqual(['n1']);
    expect(next.room).toBe(2);
  });
  it('returns from a grid room opened by the record to the record', () => {
    const view = initialView({record: {scope: {}, returnNode: null}, recordDestination: true, room: 4});
    const {view: next, focus} = close(view);
    expect(next).toMatchObject({recordDestination: false, room: null});
    expect(next.record).not.toBeNull();
    expect(focus).toBe('record');
  });
  it('returns from a nested record to inspecting its material, without moving focus', () => {
    const view = initialView({record: {scope: {}, returnNode: null}, nestedRecord: 'n7'});
    const {view: next, focus} = close(view);
    expect(next.nestedRecord).toBeNull();
    expect(overlay(next, 'inspect')?.id).toBe('n7');
    expect(focus).toBe('none');
  });
  it('closes the record and inspects the material it was opened from', () => {
    const next = close(initialView({record: {scope: {node_id: 'n2'}, returnNode: 'n2'}})).view;
    expect(next.record).toBeNull();
    expect(overlay(next, 'inspect')?.id).toBe('n2');
  });
  it('clears the acting set before leaving a grid room', () => {
    const view = initialView({acting: ['n1'], room: 1});
    const once = close(view).view;
    expect(once).toMatchObject({acting: [], room: 1});
    expect(close(once).view.room).toBeNull();
  });
  it('returns a grid room opened from a sequence to Sequences', () => {
    const next = close(initialView({room: 1, sequenceReturn: 's1'})).view;
    expect(next).toMatchObject({room: null, panel: {kind: 'sequences'}, sequenceReturn: 's1'});
  });
  it('changes nothing with nothing open', () => {
    const view = initialView();
    expect(close(view)).toEqual({view, focus: 'origin'});
  });
});

describe('openRecord', () => {
  it('opens the record over the project and closes inspection, picker, acting and rooms', () => {
    const view = initialView({room: 2, acting: ['a'], panel: {kind: 'backup'},
      overlays: [{kind: 'inspect', id: 'n1'}, {kind: 'picker', direction, left: 1, top: 2}, {kind: 'projectEdit'}]});
    const next = openRecord(view, {direction_id: 'd1'});
    expect(next).toMatchObject({record: {scope: {direction_id: 'd1'}, returnNode: null}, room: null, acting: [], panel: null});
    expect(next.overlays).toEqual([{kind: 'projectEdit'}]);
  });
  it('opens a nested record from inspection inside the record', () => {
    const view = initialView({record: {scope: {}, returnNode: null}, overlays: [{kind: 'inspect', id: 'n1'}]});
    const next = openRecord(view, {node_id: 'n1'}, 'n1');
    expect(next.nestedRecord).toBe('n1');
    expect(next.record?.scope).toEqual({});
    expect(overlay(next, 'inspect')).toBeUndefined();
  });
});

describe('view store', () => {
  it('opens one room over the canvas at a time and closes the record beneath it', () => {
    const store = createViewStore({record: {scope: {}, returnNode: null}, acting: ['n1'], room: 3});
    store.getState().showDeliveries();
    expect(store.getState()).toMatchObject({panel: {kind: 'delivery', ids: []}, record: null, acting: [], room: 3});
    store.getState().showBackup();
    expect(store.getState()).toMatchObject({panel: {kind: 'backup'}, room: null});
  });
  it('delivers and returns the acting set', () => {
    const store = createViewStore({acting: ['a', 'b']});
    store.getState().deliverActing();
    expect(store.getState()).toMatchObject({panel: {kind: 'delivery', ids: ['a', 'b']}, acting: []});
    store.getState().act(['c']);
    store.getState().returnActing();
    expect(store.getState()).toMatchObject({panel: {kind: 'returned', ids: ['c']}, acting: []});
  });
  it('keeps one inspection, replacing the material it shows', () => {
    const store = createViewStore();
    store.getState().inspect('a');
    store.getState().inspect('b');
    expect(store.getState().overlays).toEqual([{kind: 'inspect', id: 'b'}]);
  });
  it('closes the top layer and reports where focus returns', () => {
    const store = createViewStore({record: {scope: {}, returnNode: null}});
    store.getState().inspect('a');
    expect(store.getState().closeTop()).toBe('record');
    expect(store.getState().overlays).toEqual([]);
  });
  it('leaves a grid room for Sequences, or for the record it was opened from', () => {
    const fromSequence = createViewStore();
    fromSequence.getState().showRunFromSequence(2, 's1');
    expect(fromSequence.getState().leaveRoom()).toBe('none');
    expect(fromSequence.getState()).toMatchObject({room: null, panel: {kind: 'sequences'}});
    const fromRecord = createViewStore({record: {scope: {}, returnNode: null}, acting: ['n']});
    fromRecord.getState().showFromRecord('tray');
    expect(fromRecord.getState().leaveRoom()).toBe('record');
    expect(fromRecord.getState()).toMatchObject({room: null, recordDestination: false, acting: []});
  });
  it('toggles the tray and clears the record and any room over the canvas', () => {
    const store = createViewStore({record: {scope: {}, returnNode: null}, panel: {kind: 'reuse'}, sequenceReturn: 's1'});
    store.getState().toggleTray();
    expect(store.getState()).toMatchObject({room: 'tray', record: null, panel: null, sequenceReturn: null});
    store.getState().toggleTray();
    expect(store.getState().room).toBeNull();
  });
  it('opens a new preparation in the drawer and closes the acting set and picker', () => {
    const store = createViewStore({acting: ['a'], overlays: [{kind: 'picker', direction, left: 0, top: 0}]});
    store.getState().prepared('d9');
    expect(store.getState()).toMatchObject({drawer: 'd9', acting: [], overlays: []});
  });
});
