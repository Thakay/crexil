import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { spaceActivates, useCommandKey, useEscapeBase, useEscapeKey, useEscapeLayer, type EscapeHandler } from './keys';

function Surface({base}: {base?: () => void}) {
  useEscapeKey();
  return base ? <Base close={base}/> : null;
}
function Base({close}: {close: () => void}) {
  useEscapeBase(close);
  return <input aria-label="field"/>;
}
function Layer({handle}: {handle: EscapeHandler}) {
  useEscapeLayer(handle);
  return null;
}
function Command({run, ready}: {run: () => void; ready: () => boolean}) {
  useCommandKey('n', run, ready);
  return <input aria-label="typing"/>;
}

const press = (key: string, init: KeyboardEventInit = {}) => {
  const code = key === 'Escape' ? 'Escape' : `Key${key.toUpperCase()}`;
  fireEvent.keyDown(document.activeElement ?? document.body, {key, code, ...init});
};

afterEach(cleanup);

describe('Escape', () => {
  it('closes the top layer of the room when no layer claims it', () => {
    const base = vi.fn();
    render(<Surface base={base}/>);
    press('Escape');
    expect(base).toHaveBeenCalledTimes(1);
  });
  it('lets the newest claiming layer handle it, and nothing beneath', () => {
    const base = vi.fn(), older = vi.fn(() => true), newer = vi.fn(() => true);
    render(<><Surface base={base}/><Layer handle={older}/><Layer handle={newer}/></>);
    press('Escape');
    expect(newer).toHaveBeenCalledTimes(1);
    expect(older).not.toHaveBeenCalled();
    expect(base).not.toHaveBeenCalled();
  });
  it('passes Escape down when a layer does not claim it', () => {
    const base = vi.fn(), guard = vi.fn(() => false);
    render(<><Surface base={base}/><Layer handle={guard}/></>);
    press('Escape');
    expect(guard).toHaveBeenCalledTimes(1);
    expect(base).toHaveBeenCalledTimes(1);
  });
  it('still closes while typing in a field', () => {
    const base = vi.fn();
    const view = render(<Surface base={base}/>);
    view.getByLabelText('field').focus();
    press('Escape');
    expect(base).toHaveBeenCalledTimes(1);
  });
  it('forgets a layer once it unmounts', () => {
    const base = vi.fn(), layer = vi.fn(() => true);
    const view = render(<><Surface base={base}/><Layer handle={layer}/></>);
    view.rerender(<Surface base={base}/>);
    press('Escape');
    expect(layer).not.toHaveBeenCalled();
    expect(base).toHaveBeenCalledTimes(1);
  });
});

describe('plain-key commands', () => {
  it('run on the bare key when ready', () => {
    const run = vi.fn();
    render(<Command run={run} ready={() => true}/>);
    press('n');
    expect(run).toHaveBeenCalledTimes(1);
  });
  it('never run with Ctrl, Cmd or Alt held', () => {
    const run = vi.fn();
    render(<Command run={run} ready={() => true}/>);
    press('n', {ctrlKey: true});
    press('n', {metaKey: true});
    press('n', {altKey: true});
    expect(run).not.toHaveBeenCalled();
  });
  it('never run while typing in a field', () => {
    const run = vi.fn();
    const view = render(<Command run={run} ready={() => true}/>);
    view.getByLabelText('typing').focus();
    press('n');
    expect(run).not.toHaveBeenCalled();
  });
  it('check readiness at the moment of the key, so a busy room ignores a repeat', () => {
    let busy = false;
    const run = vi.fn(() => {busy = true;});
    render(<Command run={run} ready={() => !busy}/>);
    press('n');
    press('n');
    expect(run).toHaveBeenCalledTimes(1);
  });
});

describe('Space and focused controls (WCAG 2.1.1)', () => {
  function Space({run}: {run: () => void}) {
    useCommandKey('space', run, () => true, spaceActivates);
    return <><button>Loop in</button><button data-audio-face="">Act on 0A, audio, 4.4 s</button><span tabIndex={0}>plain</span>
      <a href="#here">Link</a><div role="switch" tabIndex={0} aria-checked="false">Switch</div></>;
  }
  const space = () => fireEvent.keyDown(document.activeElement ?? document.body, {key: ' ', code: 'Space'});
  it('leaves Space to a focused button, link or switch, which Space activates', () => {
    const run = vi.fn();
    const view = render(<Space run={run}/>);
    for (const name of ['Loop in', 'Link', 'Switch']) {
      (view.getByText(name) as HTMLElement).focus();
      expect(space()).toBe(true);               // not prevented: the control keeps its own activation
    }
    expect(run).not.toHaveBeenCalled();
  });
  it("keeps Space for an audio take's face and for anything that is not a control", () => {
    const run = vi.fn();
    const view = render(<Space run={run}/>);
    view.getByText('Act on 0A, audio, 4.4 s').focus();
    expect(space()).toBe(false);
    view.getByText('plain').focus();
    space();
    expect(run).toHaveBeenCalledTimes(2);
  });
});
