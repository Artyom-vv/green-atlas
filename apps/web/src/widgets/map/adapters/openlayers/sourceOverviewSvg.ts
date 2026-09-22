const NS = 'http://www.w3.org/2000/svg';

/** Rebuild only our generated path vocabulary; never mount arbitrary CAD markup. */
export function createOverviewSvg(markup: string) {
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
    for (const key of ['fill', 'stroke']) {
      const value = input.getAttribute(key) ?? 'none';
      group.setAttribute(key, /^#[0-9a-f]{6}$/i.test(value) ? value : 'none');
    }
    group.setAttribute('fill-opacity', '0.14');
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
  return {
    element: svg,
    render(size: number[], transform: number[]) {
      svg.setAttribute('width', String(size[0]));
      svg.setAttribute('height', String(size[1]));
      svg.setAttribute('viewBox', `0 0 ${size[0]} ${size[1]}`);
      // Captured SVG coordinates already negate CAD Y. Undo that in the map
      // transform; OL supplies CSS pixels, including pan, zoom and rotation.
      const [a, b, c, d, e, f] = transform;
      world.setAttribute('transform', `matrix(${a} ${b} ${-c} ${-d} ${e} ${f})`);
      return svg;
    },
  };
}
