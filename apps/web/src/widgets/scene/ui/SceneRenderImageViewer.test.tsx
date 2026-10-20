import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SceneRenderImageViewer } from './SceneRenderImageViewer';

let viewportWidth = 500;
let viewportHeight = 300;
let resize: () => void;

function loadImage(width = 1000, height = 600) {
  const image = screen.getByAltText('Рендер 3D-сцены') as HTMLImageElement;
  Object.defineProperties(image, {
    naturalWidth: { value: width, configurable: true },
    naturalHeight: { value: height, configurable: true },
  });
  fireEvent.load(image);
  return image;
}

describe('SceneRenderImageViewer', () => {
  beforeEach(() => {
    viewportWidth = 500;
    viewportHeight = 300;
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(callback: () => void) {
          resize = callback;
        }
        observe() {}
        disconnect() {}
      },
    );
    vi.stubGlobal(
      'PointerEvent',
      class extends MouseEvent {
        pointerId: number;
        constructor(type: string, init: PointerEventInit = {}) {
          super(type, init);
          this.pointerId = init.pointerId ?? 1;
        }
      },
    );
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(
      function (this: HTMLElement) {
        return {
          x: 0,
          y: 0,
          width:
            this.getAttribute('aria-label') === 'Просмотр рендера'
              ? viewportWidth
              : 0,
          height: viewportHeight,
          top: 0,
          left: 0,
          right: viewportWidth,
          bottom: viewportHeight,
          toJSON: () => ({}),
        };
      },
    );
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('fits the natural PNG dimensions, updates fit on resize and preserves 100% pixels', () => {
    render(
      <SceneRenderImageViewer src="/render.png" width={1920} height={1080} />,
    );
    expect(
      screen.getByRole('button', { name: 'Увеличить изображение' }),
    ).toBeDisabled();
    const image = loadImage();
    expect(image).toHaveStyle({ width: '500px', height: '300px' });
    expect(screen.getByText('1000 × 600 px')).toBeInTheDocument();
    viewportWidth = 400;
    viewportHeight = 200;
    act(() => resize());
    expect(image.style.height).toBe('200px');
    fireEvent.click(
      screen.getByRole('button', { name: 'Исходный размер, 100%' }),
    );
    expect(image).toHaveStyle({ width: '1000px', height: '600px' });
    viewportWidth = 300;
    viewportHeight = 150;
    act(() => resize());
    expect(image).toHaveStyle({ width: '1000px', height: '600px' });
  });

  it('supports keyboard zoom, pan and fit only from the focused image region', () => {
    render(
      <SceneRenderImageViewer src="/render.png" width={1000} height={600} />,
    );
    const image = loadImage();
    const region = screen.getByRole('region', { name: 'Просмотр рендера' });
    expect(region).toHaveAttribute('tabindex', '0');
    expect(region).toHaveAccessibleDescription(/плюс и минус/);
    region.focus();
    fireEvent.keyDown(region, { key: '1' });
    expect(region).toHaveClass('cursor-grab');
    fireEvent.keyDown(region, { key: 'ArrowRight' });
    expect(image.style.transform).toContain('-40px');
    fireEvent.keyDown(region, { key: '+' });
    expect(image.style.width).toBe('1250px');
    fireEvent.keyDown(region, { key: '-' });
    expect(image.style.width).toBe('1000px');
    fireEvent.keyDown(region, { key: '0' });
    expect(image.style.width).toBe('500px');
    expect(region).toHaveClass('cursor-default');
    expect(image.style.transform).toBe(
      'translate(calc(-50% + 0px), calc(-50% + 0px))',
    );
  });

  it('captures a drag only when enlarged, clamps its edges and releases on cancellation', () => {
    render(
      <SceneRenderImageViewer src="/render.png" width={1000} height={600} />,
    );
    const image = loadImage();
    const region = screen.getByRole('region', { name: 'Просмотр рендера' });
    const capture = vi.fn();
    const release = vi.fn();
    Object.assign(region, {
      setPointerCapture: capture,
      hasPointerCapture: () => true,
      releasePointerCapture: release,
    });
    fireEvent.pointerDown(region, {
      button: 0,
      pointerId: 5,
      clientX: 100,
      clientY: 100,
    });
    expect(capture).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole('button', { name: 'Исходный размер, 100%' }),
    );
    fireEvent.pointerDown(region, {
      button: 0,
      pointerId: 5,
      clientX: 100,
      clientY: 100,
    });
    expect(capture).toHaveBeenCalledWith(5);
    expect(region).toHaveClass('cursor-grabbing');
    fireEvent.pointerMove(region, {
      pointerId: 5,
      clientX: 5000,
      clientY: -5000,
    });
    expect(image.style.transform).toBe(
      'translate(calc(-50% + 250px), calc(-50% + -150px))',
    );
    fireEvent.pointerCancel(region, { pointerId: 5 });
    expect(release).toHaveBeenCalledWith(5);
    expect(region).toHaveClass('cursor-grab');
  });

  it('keeps fit responsive after an inapplicable pan and leaves browser shortcuts alone', () => {
    render(
      <SceneRenderImageViewer src="/render.png" width={1000} height={600} />,
    );
    const image = loadImage();
    const region = screen.getByRole('region', { name: 'Просмотр рендера' });
    fireEvent.keyDown(region, { key: 'ArrowRight' });
    viewportWidth = 250;
    viewportHeight = 150;
    act(() => resize());
    expect(image.style.width).toBe('250px');
    fireEvent.keyDown(region, { key: '+', ctrlKey: true });
    fireEvent.keyDown(region, { key: '1', metaKey: true });
    expect(image.style.width).toBe('250px');
  });

  it('zooms at the wheel pointer, bounds each step and prevents page scrolling only for a loaded PNG', () => {
    render(
      <SceneRenderImageViewer src="/render.png" width={1000} height={600} />,
    );
    const region = screen.getByRole('region', { name: 'Просмотр рендера' });
    const wheel = (deltaY: number) => {
      const event = new WheelEvent('wheel', {
        bubbles: true,
        cancelable: true,
        clientX: 375,
        clientY: 75,
        deltaY,
      });
      fireEvent(region, event);
      return event;
    };
    expect(wheel(-100).defaultPrevented).toBe(false);
    const image = loadImage();
    fireEvent.click(
      screen.getByRole('button', { name: 'Исходный размер, 100%' }),
    );
    expect(wheel(-10000).defaultPrevented).toBe(true);
    const scale = Number.parseFloat(image.style.width) / 1000;
    expect(scale).toBeCloseTo(Math.exp(0.25));
    const offsets = [
      ...image.style.transform.matchAll(/calc\(-50% \+ (-?[\d.]+)px\)/g),
    ].map((match) => Number(match[1]));
    expect((125 - offsets[0]) / scale).toBeCloseTo(125);
    expect((-75 - offsets[1]) / scale).toBeCloseTo(-75);
    fireEvent.error(image);
    expect(wheel(-100).defaultPrevented).toBe(false);
  });

  it('retries the same PNG without a render request and resets view for a new source', () => {
    const { rerender } = render(
      <SceneRenderImageViewer src="/render.png" width={1000} height={600} />,
    );
    const first = loadImage();
    fireEvent.click(
      screen.getByRole('button', { name: 'Исходный размер, 100%' }),
    );
    fireEvent.error(first);
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Не удалось загрузить PNG',
    );
    expect(
      screen.getByRole('button', { name: 'Увеличить изображение' }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }));
    const second = screen.getByAltText('Рендер 3D-сцены');
    expect(second).not.toBe(first);
    expect(second).toHaveAttribute('src', '/render.png');
    loadImage();
    fireEvent.click(
      screen.getByRole('button', { name: 'Исходный размер, 100%' }),
    );
    rerender(
      <SceneRenderImageViewer src="/next.png" width={400} height={800} />,
    );
    expect(screen.getByRole('status')).toHaveTextContent('Загружаем PNG');
    const next = loadImage(400, 800);
    expect(next).toHaveStyle({ width: '150px', height: '300px' });
    expect(next.style.transform).toBe(
      'translate(calc(-50% + 0px), calc(-50% + 0px))',
    );
  });
});
