import type { CadAppearanceMode } from '../../../model/cadSource';
import { readOverviewPaths } from './sourceOverviewSvg';

/** Компиляция безопасных контуров обзора; кадром и анимацией владеет OpenLayers */
export function createOverviewCanvas(
  markup: string,
  mode: CadAppearanceMode = 'design',
) {
  const groups = readOverviewPaths(markup, mode).map((group) => ({
    ...group,
    paths: group.paths.map((d) => {
      const numbers = (
        d.match(/[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?/gi) ?? []
      ).map(Number);
      const bounds = [Infinity, Infinity, -Infinity, -Infinity];
      for (let i = 0; i < numbers.length; i += 2) {
        bounds[0] = Math.min(bounds[0], numbers[i]);
        bounds[1] = Math.min(bounds[1], numbers[i + 1]);
        bounds[2] = Math.max(bounds[2], numbers[i]);
        bounds[3] = Math.max(bounds[3], numbers[i + 1]);
      }
      // Старые обзоры могут содержать относительные команды: их нельзя
      // отсекать по границам, вычисленным из абсолютных координат.
      if (/[ml]/.test(d))
        bounds.splice(0, 4, -Infinity, -Infinity, Infinity, Infinity);
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
  return {
    element: canvas,
    render(
      size: number[],
      transform: number[],
      ratio = window.devicePixelRatio || 1,
    ) {
      const [width, height] = size;
      canvas.width = Math.ceil(width * ratio);
      canvas.height = Math.ceil(height * ratio);
      const [a, b, originalC, originalD, tx, ty] = transform;
      const c = -originalC,
        d = -originalD,
        e = tx,
        f = ty;
      const determinant = a * d - b * c;
      if (!Number.isFinite(determinant) || determinant === 0) return canvas;
      const extent = [Infinity, Infinity, -Infinity, -Infinity];
      for (const [px, py] of [
        [0, 0],
        [width, 0],
        [0, height],
        [width, height],
      ]) {
        const x = (d * (px - e) - c * (py - f)) / determinant;
        const y = (-b * (px - e) + a * (py - f)) / determinant;
        extent[0] = Math.min(extent[0], x);
        extent[1] = Math.min(extent[1], y);
        extent[2] = Math.max(extent[2], x);
        extent[3] = Math.max(extent[3], y);
      }
      const strokeWidth = 0.8 / Math.hypot(a, b);
      context.setTransform(
        a * ratio,
        b * ratio,
        c * ratio,
        d * ratio,
        e * ratio,
        f * ratio,
      );
      context.lineWidth = strokeWidth;
      context.lineJoin = 'round';
      for (const group of groups) {
        if (group.fill !== 'none') context.fillStyle = group.fill;
        if (group.stroke !== 'none') context.strokeStyle = group.stroke;
        for (const { path, bounds } of group.paths) {
          if (
            bounds[2] + strokeWidth < extent[0] ||
            bounds[0] - strokeWidth > extent[2] ||
            bounds[3] + strokeWidth < extent[1] ||
            bounds[1] - strokeWidth > extent[3]
          )
            continue;
          // Отверстия, перекрытия областей и порядок наложения сохраняются.
          if (group.fill !== 'none') {
            context.globalAlpha = group.alpha;
            context.fill(path, 'evenodd');
          }
          context.globalAlpha = 1;
          if (group.stroke !== 'none') context.stroke(path);
        }
      }
      return canvas;
    },
  };
}
