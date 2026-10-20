import Map from 'ol/Map';
import BaseEvent from 'ol/events/Event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { attachMapInputEvents } from './attachMapInputEvents';

const frames = new globalThis.Map<number, FrameRequestCallback>();
const maps: Map[] = [];
const detachments: (() => void)[] = [];
let nextFrame = 0;

beforeEach(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    frames.set(++nextFrame, callback);
    return nextFrame;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
});
afterEach(() => {
  detachments.splice(0).forEach((detach) => detach());
  maps.splice(0).forEach((map) => map.dispose());
  frames.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function flushFrames() {
  const pending = [...frames.values()];
  frames.clear();
  pending.forEach((callback) => callback(0));
}

function fixture(existingMap?: Map) {
  const map = existingMap ?? new Map({ controls: [], interactions: [] });
  if (!existingMap) maps.push(map);
  const options = {
    map,
    target: document.createElement('div'),
    spacePanRef: { current: false },
    onClick: vi.fn(),
    onSingleClick: vi.fn(),
    onPointerMove: vi.fn(),
    onPointerLeave: vi.fn(),
    onNavigationStart: vi.fn(),
  };
  const detach = attachMapInputEvents(options);
  detachments.push(detach);
  flushFrames();
  return { ...options, detach };
}

describe('map input event lifetime', () => {
  it('does not pan while typing and clears the temporary hand after losing focus', () => {
    const { target, spacePanRef } = fixture();
    const input = document.createElement('input');
    document.body.append(input);
    input.dispatchEvent(
      new KeyboardEvent('keydown', {
        code: 'Space',
        bubbles: true,
        cancelable: true,
      }),
    );
    expect(spacePanRef.current).toBe(false);
    const space = new KeyboardEvent('keydown', {
      code: 'Space',
      cancelable: true,
    });
    window.dispatchEvent(space);
    expect(space.defaultPrevented).toBe(true);
    expect(target.style.cursor).toBe('grab');
    window.dispatchEvent(new Event('blur'));
    expect(spacePanRef.current).toBe(false);
    expect(target.style.cursor).toBe('');
    input.remove();
  });

  it('never turns a temporary navigation click into a delayed planting action after Space release', () => {
    const { map, target, onClick, onSingleClick } = fixture();
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space' }));
    target.dispatchEvent(
      new MouseEvent('pointerdown', { button: 0, cancelable: true }),
    );
    map.dispatchEvent('click');
    window.dispatchEvent(new KeyboardEvent('keyup', { code: 'Space' }));
    map.dispatchEvent('singleclick');
    expect(onClick).not.toHaveBeenCalled();
    expect(onSingleClick).not.toHaveBeenCalled();
    target.dispatchEvent(new MouseEvent('pointerdown', { button: 0 }));
    map.dispatchEvent('singleclick');
    expect(onSingleClick).toHaveBeenCalledOnce();
  });
  it.each(['mouseleave', 'movestart'] as const)(
    'does not replay a queued pointer move after %s clears its surface',
    (eventType) => {
      const { map, target, onPointerMove } = fixture();
      map.dispatchEvent('pointermove');
      if (eventType === 'mouseleave')
        target.dispatchEvent(new MouseEvent(eventType));
      else map.dispatchEvent(eventType);
      flushFrames();
      expect(onPointerMove.mock.calls.length).toBe(0);
      map.dispatchEvent('pointermove');
      flushFrames();
      expect(onPointerMove).toHaveBeenCalledOnce();
    },
  );

  it('delivers click and singleclick independently without waiting for the pointer frame', () => {
    const { map, onClick, onSingleClick } = fixture();
    const click = new BaseEvent('click');
    const singleClick = new BaseEvent('singleclick');
    map.dispatchEvent(click);
    expect(onClick).toHaveBeenCalledExactlyOnceWith(click);
    expect(onSingleClick).not.toHaveBeenCalled();
    map.dispatchEvent(singleClick);
    expect(onSingleClick).toHaveBeenCalledExactlyOnceWith(singleClick);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('processes the latest pointer event once per frame and permits the next frame', () => {
    const { map, onPointerMove } = fixture();
    const first = new BaseEvent('pointermove');
    const last = new BaseEvent('pointermove');
    map.dispatchEvent(first);
    map.dispatchEvent(last);
    expect(onPointerMove).not.toHaveBeenCalled();
    expect(frames.size).toBe(1);
    flushFrames();
    expect(onPointerMove).toHaveBeenCalledExactlyOnceWith(last);
    map.dispatchEvent(first);
    flushFrames();
    expect(onPointerMove.mock.calls.map(([event]) => event)).toEqual([
      last,
      first,
    ]);
  });

  it('keeps pointer leaving and navigation cleanup as distinct immediate callbacks', () => {
    const { map, target, onPointerLeave, onNavigationStart } = fixture();
    target.dispatchEvent(new MouseEvent('mouseleave'));
    expect(onPointerLeave).toHaveBeenCalledOnce();
    expect(onNavigationStart).not.toHaveBeenCalled();
    map.dispatchEvent('movestart');
    expect(onNavigationStart).toHaveBeenCalledOnce();
    expect(onPointerLeave).toHaveBeenCalledOnce();
  });

  it('tracks Space transitions and prevents page zoom only on its own target', () => {
    const { target, spacePanRef, detach } = fixture();
    window.dispatchEvent(
      new KeyboardEvent('keydown', { code: 'Space', repeat: true }),
    );
    expect(spacePanRef.current).toBe(false);
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space' }));
    expect(spacePanRef.current).toBe(true);
    window.dispatchEvent(new KeyboardEvent('keyup', { code: 'KeyA' }));
    expect(spacePanRef.current).toBe(true);
    window.dispatchEvent(new KeyboardEvent('keyup', { code: 'Space' }));
    expect(spacePanRef.current).toBe(false);
    const wheel = new WheelEvent('wheel', { cancelable: true });
    target.dispatchEvent(wheel);
    expect(wheel.defaultPrevented).toBe(true);
    detach();
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space' }));
    expect(spacePanRef.current).toBe(false);
    const detachedWheel = new WheelEvent('wheel', { cancelable: true });
    target.dispatchEvent(detachedWheel);
    expect(detachedWheel.defaultPrevented).toBe(false);
  });

  it('cancels its pending frame and listeners without disposing the shared map or another listener', () => {
    const options = fixture();
    const otherListener = vi.fn();
    options.map.on('click', otherListener);
    const dispose = vi.spyOn(options.map, 'dispose');
    options.map.dispatchEvent('pointermove');
    options.detach();
    flushFrames();
    options.map.dispatchEvent('click');
    options.map.dispatchEvent('singleclick');
    options.map.dispatchEvent('pointermove');
    options.map.dispatchEvent('movestart');
    options.target.dispatchEvent(new MouseEvent('mouseleave'));
    flushFrames();
    expect(options.onPointerMove).not.toHaveBeenCalled();
    expect(options.onClick).not.toHaveBeenCalled();
    expect(options.onSingleClick).not.toHaveBeenCalled();
    expect(options.onNavigationStart).not.toHaveBeenCalled();
    expect(options.onPointerLeave).not.toHaveBeenCalled();
    expect(otherListener).toHaveBeenCalledOnce();
    expect(dispose).not.toHaveBeenCalled();
  });

  it('leaves one live input owner after detach and reattachment', () => {
    const previous = fixture();
    previous.map.dispatchEvent('pointermove');
    previous.detach();
    const current = fixture(previous.map);
    current.map.dispatchEvent('pointermove');
    current.map.dispatchEvent('singleclick');
    flushFrames();
    expect(previous.onPointerMove).not.toHaveBeenCalled();
    expect(previous.onSingleClick).not.toHaveBeenCalled();
    expect(current.onPointerMove).toHaveBeenCalledOnce();
    expect(current.onSingleClick).toHaveBeenCalledOnce();
  });
});
