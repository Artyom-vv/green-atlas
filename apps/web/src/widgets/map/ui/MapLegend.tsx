import type { Layer } from '@green/api-client';
import { ChevronDown } from 'lucide-react';
import {
  MAP_DESIGN_LEGEND,
  MAP_DESIGN_PALETTE,
} from '../model/mapDesignPalette';

export function MapLegend({ layers }: { layers: Layer[] }) {
  const visible = new Set(
    layers
      .filter((layer) => layer.visible && layer.object_count > 0)
      .map((layer) => layer.mapped_kind),
  );
  const items = MAP_DESIGN_LEGEND.filter((item) => visible.has(item.kind));
  if (!items.length) return null;
  return (
    <details className="group relative text-xs">
      <summary className="flex min-h-8 cursor-pointer list-none items-center gap-2 rounded-lg border border-neutral-200 bg-white px-3 font-medium shadow-sm focus-visible:outline-2 focus-visible:outline-blue-600 [&::-webkit-details-marker]:hidden">
        Легенда{' '}
        <ChevronDown
          size={14}
          aria-hidden="true"
          className="group-open:rotate-180"
        />
      </summary>
      <div
        className="absolute top-full right-0 z-50 mt-2 w-64 rounded-xl border border-neutral-200 bg-white p-4 shadow-lg"
        aria-label="Обозначения проектного вида"
      >
        <ul className="m-0 grid list-none gap-3 p-0">
          {items.map(({ kind, label, shape }) => {
            const [fill, stroke] = MAP_DESIGN_PALETTE[kind];
            return (
              <li key={kind} className="flex items-center gap-3">
                <span
                  aria-hidden="true"
                  className="inline-block w-6 shrink-0"
                  style={
                    shape === 'area'
                      ? {
                          height: 14,
                          background: fill,
                          border: `1px solid ${stroke}`,
                          borderRadius: 3,
                        }
                      : { borderTop: `2px dashed ${stroke}` }
                  }
                />
                <span>{label}</span>
              </li>
            );
          })}
        </ul>
        <p className="mt-4 mb-0 border-t border-neutral-100 pt-3 leading-4 text-neutral-500">
          Допустимые места показывает расчёт.
        </p>
      </div>
    </details>
  );
}
