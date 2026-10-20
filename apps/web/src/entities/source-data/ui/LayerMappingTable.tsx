import type { Layer, LayerMapping, LayerRecognition } from '@green/api-client';
import { cx } from '@green/ui';
import type { FC } from 'react';
import { selectPlanningBoundary, toLayerMapping } from '../model/layerKinds';
import { LayerMappingRow } from './LayerMappingRow';
import { LAYER_MAPPING_COLUMNS } from './layerMappingLayout';

export interface LayerMappingTableProps {
  layers: Layer[];
  mappings: Record<string, LayerMapping>;
  onChange: (next: Record<string, LayerMapping>) => void;
  readOnly?: boolean;
  disabled?: boolean;
  recognition?: LayerRecognition;
  stickyHeader?: boolean;
  onBoundaryReview?: () => void;
}

/** Both views use the same row decisions and columns; only membership changes. */
export const LayerMappingTable: FC<LayerMappingTableProps> = ({
  layers,
  mappings,
  onChange,
  stickyHeader,
  ...props
}) => (
  <div className="@container min-w-0">
    {layers.length > 0 && (
      <div
        aria-hidden="true"
        className={cx(
          'hidden items-center gap-4 border-b border-neutral-200 bg-neutral-100 px-4 py-3 text-xs leading-4 text-neutral-500 min-[720px]:px-6 @3xl:grid',
          LAYER_MAPPING_COLUMNS,
          stickyHeader && 'sticky top-0 z-10',
        )}
      >
        <span>Исходный слой</span>
        <span>Тип на чертеже</span>
        <span>Роль в расчёте</span>
        <span>Решение</span>
      </div>
    )}
    <ul aria-label="Слои чертежа" className="m-0 list-none p-0">
      {layers.map((layer) => (
        <LayerMappingRow
          key={layer.id}
          {...props}
          layer={layer}
          mapping={mappings[layer.id] ?? toLayerMapping(layer)}
          onChange={(next) => {
            const updated = { ...mappings, [layer.id]: next };
            onChange(
              next.kind === 'site_border'
                ? selectPlanningBoundary(updated, layer.id)
                : updated,
            );
          }}
        />
      ))}
    </ul>
  </div>
);
