import { MAP_DESIGN_PALETTE } from '../../../model/mapDesignPalette';
import type { CadAppearanceMode } from '../../../model/cadSource';
import { mapDesignFillOrder } from '../../../model/mapDesignDrawOrder';

const NS = 'http://www.w3.org/2000/svg';

/** Безопасные контуры CAD-обзора для Canvas и SVG без промежуточной копии DOM */
export function readOverviewPaths(
  markup: string,
  mode: CadAppearanceMode = 'design',
) {
  const parsed = new DOMParser().parseFromString(markup, 'image/svg+xml');
  const inputs = [...parsed.querySelectorAll('svg > g')];
  if (mode === 'design') {
    const order = (input: Element) =>
      input.getAttribute('data-shape') === 'area'
        ? mapDesignFillOrder(input.getAttribute('data-kind') ?? undefined)
        : -1;
    inputs.sort((first, second) => order(first) - order(second));
  }
  return inputs.map((input) => {
    const palette =
      mode === 'design'
        ? MAP_DESIGN_PALETTE[input.getAttribute('data-kind') ?? '']
        : undefined;
    const area = input.getAttribute('data-shape') === 'area';
    const color = (key: 'fill' | 'stroke') => {
      const value = palette
        ? key === 'stroke'
          ? palette[1]
          : area
            ? palette[0]
            : 'none'
        : (input.getAttribute(key) ?? 'none');
      return /^#[0-9a-f]{6}$/i.test(value) ? value : 'none';
    };
    return {
      fill: color('fill'),
      stroke: color('stroke'),
      alpha: palette ? 1 : 0.14,
      paths: [...input.querySelectorAll(':scope > path')]
        .map((path) => path.getAttribute('d') ?? '')
        .filter((d) => /^[MLZmlz0-9.,eE+\s-]*$/.test(d)),
    };
  });
}

/** SVG-представление обзора из допустимых контуров и цветов CAD */
export function createOverviewSvg(
  markup: string,
  mode: CadAppearanceMode = 'design',
) {
  const svg = document.createElementNS(NS, 'svg');
  const world = document.createElementNS(NS, 'g');
  svg.append(world);
  // Единый графический объект не заставляет WebKit обходить каждый контур
  // при чтении дерева доступности, как это происходит с aria-hidden.
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Подоснова чертежа');
  svg.setAttribute('focusable', 'false');
  svg.style.pointerEvents = 'none';
  svg.style.position = 'absolute';
  svg.style.inset = '0';
  svg.style.overflow = 'hidden';
  for (const input of readOverviewPaths(markup, mode)) {
    const group = document.createElementNS(NS, 'g');
    group.setAttribute('fill', input.fill);
    group.setAttribute('stroke', input.stroke);
    group.setAttribute('fill-opacity', String(input.alpha));
    group.setAttribute('fill-rule', 'evenodd');
    group.setAttribute('stroke-linejoin', 'round');
    for (const d of input.paths) {
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
      // При перемещении используем готовый кадр с запасом по краям.
      // Масштабирование, поворот и завершение жеста обновляют точные контуры.
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
      // В захваченном SVG ось CAD Y уже инвертирована. Преобразование карты
      // отменяет инверсию и задаёт положение в экранных пикселях.
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
