import type { CadAppearanceMode } from '../../model/cadSource';
import { createOverviewSvg } from './sourceOverviewSvg';

/** Compile the sanitized display vectors once, without attaching CAD nodes to the page. */
export function createOverviewCanvas(markup: string, mode: CadAppearanceMode = 'design') {
  const source = createOverviewSvg(markup, mode).element;
  const groups = [...source.querySelectorAll('g > g')].map((group) => ({
    fill: group.getAttribute('fill') ?? 'none',
    stroke: group.getAttribute('stroke') ?? 'none',
    alpha: Number(group.getAttribute('fill-opacity') ?? 1),
    paths: [...group.querySelectorAll('path')].map((path) => {
      const d = path.getAttribute('d')!;
      const numbers = (d.match(/[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?/gi) ?? []).map(Number);
      const bounds = [Infinity, Infinity, -Infinity, -Infinity];
      for (let i = 0; i < numbers.length; i += 2) {
        bounds[0] = Math.min(bounds[0], numbers[i]);
        bounds[1] = Math.min(bounds[1], numbers[i + 1]);
        bounds[2] = Math.max(bounds[2], numbers[i]);
        bounds[3] = Math.max(bounds[3], numbers[i + 1]);
      }
      // Generated paths are absolute; preserve rendering if a compatible
      // older overview contains relative commands (no bounds culling then).
      if (/[ml]/.test(d)) bounds.splice(0, 4, -Infinity, -Infinity, Infinity, Infinity);
      return { path: new Path2D(d), bounds };
    }),
  }));
  const canvas = document.createElement('canvas');
  canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label', 'Подоснова чертежа');
  canvas.style.position = 'absolute';
  canvas.style.pointerEvents = 'none';
  const context = canvas.getContext('2d');
  if (!context) throw new Error('Не удалось открыть карту.');
  let base: { size: number[]; transform: number[]; margin: number; ratio: number } | undefined;
  return {
    element: canvas,
    render(size: number[], transform: number[], moving = false) {
      const ratio = window.devicePixelRatio || 1;
      if (base && base.ratio === ratio && size.every((v, i) => v === base!.size[i]) &&
          transform.slice(0, 4).every((v, i) => v === base!.transform[i])) {
        const dx = transform[4] - base.transform[4];
        const dy = transform[5] - base.transform[5];
        if ((moving || (dx === 0 && dy === 0)) &&
            Math.abs(dx) <= base.margin && Math.abs(dy) <= base.margin) {
          canvas.style.transform = `translate3d(${dx}px, ${dy}px, 0)`;
          return canvas;
        }
      }
      // Pan an overscanned frame, but repaint vectors locally on every zoom.
      // Stroke width never depends on the scale of the last server response.
      const margin = moving ? Math.ceil(Math.max(...size) / 2) : 0;
      const width = size[0] + 2 * margin;
      const height = size[1] + 2 * margin;
      canvas.width = Math.ceil(width * ratio);
      canvas.height = Math.ceil(height * ratio);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      canvas.style.left = canvas.style.top = `${-margin}px`;
      canvas.style.transform = '';
      const [a, b, originalC, originalD, tx, ty] = transform;
      const c = -originalC, d = -originalD, e = tx + margin, f = ty + margin;
      const determinant = a * d - b * c;
      if (!Number.isFinite(determinant) || determinant === 0) return canvas;
      const extent = [Infinity, Infinity, -Infinity, -Infinity];
      for (const [px, py] of [[0, 0], [width, 0], [0, height], [width, height]]) {
        const x = (d * (px - e) - c * (py - f)) / determinant;
        const y = (-b * (px - e) + a * (py - f)) / determinant;
        extent[0] = Math.min(extent[0], x); extent[1] = Math.min(extent[1], y);
        extent[2] = Math.max(extent[2], x); extent[3] = Math.max(extent[3], y);
      }
      const strokeWidth = 0.8 / Math.hypot(a, b);
      context.setTransform(a * ratio, b * ratio, c * ratio, d * ratio, e * ratio, f * ratio);
      context.lineWidth = strokeWidth;
      context.lineJoin = 'round';
      for (const group of groups) {
        if (group.fill !== 'none') context.fillStyle = group.fill;
        if (group.stroke !== 'none') context.strokeStyle = group.stroke;
        for (const { path, bounds } of group.paths) {
          if (bounds[2] + strokeWidth < extent[0] || bounds[0] - strokeWidth > extent[2] ||
              bounds[3] + strokeWidth < extent[1] || bounds[1] - strokeWidth > extent[3]) continue;
          // Keep each area's even-odd holes and source paint order intact.
          if (group.fill !== 'none') {
            context.globalAlpha = group.alpha;
            context.fill(path, 'evenodd');
          }
          context.globalAlpha = 1;
          if (group.stroke !== 'none') context.stroke(path);
        }
      }
      base = { size: [...size], transform: [...transform], margin, ratio };
      return canvas;
    },
  };
}
