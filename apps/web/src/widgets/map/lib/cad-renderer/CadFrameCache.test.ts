import { describe, expect, it, vi } from 'vitest';
import { CadFrameCache } from './CadFrameCache';
import type { CadFrame } from './camera';

const frame = (): CadFrame => ({
  size: [800, 600],
  viewState: { center: [100, 200], resolution: 0.5, rotation: 0 },
});

describe('CAD frame reuse', () => {
  it('renders one static CAD frame across repeated overlay renders', () => {
    const cache = new CadFrameCache();
    const draw = vi.fn();
    for (let index = 0; index < 120; index += 1) cache.render(frame(), draw);
    expect(draw).toHaveBeenCalledOnce();
  });

  it('redraws for every exact camera/viewport change, including mutable OL arrays', () => {
    const cache = new CadFrameCache();
    const current = frame();
    const draw = vi.fn();
    cache.render(current, draw);
    const changes = [
      () => (current.viewState.center[0] += 0.000001),
      () => (current.viewState.center[1] += 1),
      () => (current.viewState.resolution *= 0.5),
      () => (current.viewState.rotation += 0.2),
      () => (current.size[0] += 1),
      () => (current.size[1] += 1),
    ];
    changes.forEach((change) => {
      change();
      cache.render(current, draw);
      cache.render(current, draw);
    });
    expect(draw).toHaveBeenCalledTimes(1 + changes.length);
  });

  it('redraws after hidden zero-size frames, invalidation or source replacement', () => {
    const cache = new CadFrameCache();
    const draw = vi.fn();
    cache.render(frame(), draw);
    cache.render({ ...frame(), size: [0, 0] }, draw);
    expect(draw).toHaveBeenCalledOnce();
    cache.render(frame(), draw);
    cache.invalidate();
    cache.render(frame(), draw);
    new CadFrameCache().render(frame(), draw);
    expect(draw).toHaveBeenCalledTimes(4);
  });

  it('does not cache a failed draw', () => {
    const cache = new CadFrameCache();
    const draw = vi.fn().mockImplementationOnce(() => {
      throw new Error('render failed');
    });
    expect(() => cache.render(frame(), draw)).toThrow('render failed');
    cache.render(frame(), draw);
    expect(draw).toHaveBeenCalledTimes(2);
  });
});
