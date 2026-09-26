import { MAP_DESIGN_PALETTE } from '../../model/mapDesignPalette';
import type { CadAppearanceMode } from '../../model/cadSource';

const NS = 'http://www.w3.org/2000/svg';

/** Rebuild only our generated path vocabulary; never mount arbitrary CAD markup. */
export function createOverviewSvg(
  markup: string,
  mode: CadAppearanceMode = 'design',
) {
  const parsed = new DOMParser().parseFromString(markup, 'image/svg+xml');
  const svg = document.createElementNS(NS, 'svg');
  const world = document.createElementNS(NS, 'g');
  svg.append(world);
  svg.style.pointerEvents = 'none';
  svg.style.position = 'absolute';
  svg.style.inset = '0';
  svg.style.overflow = 'hidden';
  for (const input of parsed.querySelectorAll('svg > g')) {
    const group = document.createElementNS(NS, 'g');
    const palette =
      mode === 'design'
        ? MAP_DESIGN_PALETTE[input.getAttribute('data-kind') ?? '']
        : undefined;
    const area = input.getAttribute('data-shape') === 'area';
    for (const key of ['fill', 'stroke']) {
      const value = palette
        ? key === 'stroke'
          ? palette[1]
          : area
            ? palette[0]
            : 'none'
        : (input.getAttribute(key) ?? 'none');
      group.setAttribute(key, /^#[0-9a-f]{6}$/i.test(value) ? value : 'none');
    }
    group.setAttribute('fill-opacity', palette ? '1' : '0.14');
    group.setAttribute('fill-rule', 'evenodd');
    group.setAttribute('stroke-linejoin', 'round');
    for (const inputPath of input.querySelectorAll(':scope > path')) {
      const d = inputPath.getAttribute('d') ?? '';
      if (!/^[MLZmlz0-9.,eE+\s-]*$/.test(d)) continue;
      const path = document.createElementNS(NS, 'path');
      path.setAttribute('d', d);
      path.setAttribute('stroke-width', '0.8');
      path.setAttribute('vector-effect', 'non-scaling-stroke');
      group.append(path);
    }
    world.append(group);
  }
  let base: { size: number[]; transform: number[]; margin: number } | undefined;
  return {
    element: svg,
    render(size: number[], transform: number[], moving = false) {
      // Pan the already painted SVG via the compositor. Rewriting the world
      // matrix on every mouse frame repaints tens of thousands of CAD paths.
      // Overscan prevents empty edges; zoom/rotation/settle repaint exactly.
      if (
        moving &&
        base &&
        base.margin > 0 &&
        size.every((value, index) => value === base?.size[index]) &&
        transform
          .slice(0, 4)
          .every((value, index) => value === base?.transform[index])
      ) {
        const dx = transform[4] - base.transform[4];
        const dy = transform[5] - base.transform[5];
        if (Math.abs(dx) <= base.margin && Math.abs(dy) <= base.margin) {
          svg.style.transform = `translate3d(${dx}px, ${dy}px, 0)`;
          return svg;
        }
      }
      const margin = moving ? Math.ceil(Math.max(...size) / 2) : 0;
      svg.style.transform = '';
      svg.style.willChange = moving ? 'transform' : '';
      svg.style.left = svg.style.top = `${-margin}px`;
      svg.setAttribute('width', String(size[0] + margin * 2));
      svg.setAttribute('height', String(size[1] + margin * 2));
      svg.setAttribute(
        'viewBox',
        `0 0 ${size[0] + margin * 2} ${size[1] + margin * 2}`,
      );
      // Captured SVG coordinates already negate CAD Y. Undo that in the map
      // transform; OL supplies CSS pixels, including pan, zoom and rotation.
      const [a, b, c, d, e, f] = transform;
      world.setAttribute(
        'transform',
        `matrix(${a} ${b} ${-c} ${-d} ${e + margin} ${f + margin})`,
      );
      base = { size: [...size], transform: [...transform], margin };
      return svg;
    },
  };
}
