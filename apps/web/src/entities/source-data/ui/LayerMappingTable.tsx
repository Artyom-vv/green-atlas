import type { Layer, LayerMapping, LayerRecognition } from '@green/api-client';
import { Text } from '@green/ui';
import type { CSSProperties, FC } from 'react';
import {
  LAYER_KIND_LABELS,
  isUnclassifiedMapping,
  toLayerMapping,
  selectPlanningBoundary,
} from '../model/layerKinds';
import { LayerRoleFields } from './LayerRoleFields';

export interface LayerMappingTableProps {
  layers: Layer[];
  mappings: Record<string, LayerMapping>;
  onChange: (next: Record<string, LayerMapping>) => void;
  readOnly?: boolean;
  recognition?: LayerRecognition;
}

/** A container-responsive list keeps long CAD names and review actions inside each row. */
export const LayerMappingTable: FC<LayerMappingTableProps> = ({
  layers,
  mappings,
  onChange,
  readOnly = false,
  recognition,
}) => (
  <div className="@container">
    <div
      aria-hidden="true"
      className="hidden grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-6 border-0 border-b border-solid border-neutral-200 bg-neutral-50 px-4 py-3 text-xs font-medium text-neutral-600 @2xl:grid"
    >
      <span>Слой чертежа и объекты</span>
      <span>Тип объектов</span>
    </div>
    <ul aria-label="Слои чертежа" className="m-0 list-none p-0">
      {layers.map((layer) => {
        const mapping = mappings[layer.id] ?? toLayerMapping(layer);
        const needsReview = mapping.confirmed === false;
        return (
          <li
            key={layer.id}
            className={`grid min-w-0 gap-4 border-0 border-b border-solid border-neutral-200 p-4 last:border-b-0 @2xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] @2xl:gap-6 ${needsReview ? 'bg-amber-50/60' : 'bg-white'}`}
          >
            <div className="min-w-0">
              <div className="grid grid-cols-[12px_minmax(0,1fr)] items-start gap-2">
                <span
                  aria-hidden="true"
                  className="mt-1 size-3 rounded-xs border-2 border-(--layer-color)"
                  style={{ '--layer-color': layer.color } as CSSProperties}
                />
                <code className="font-mono text-xs leading-5 wrap-anywhere text-neutral-800">
                  {layer.source_name}
                </code>
              </div>
              <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1 pl-5 text-xs">
                <span className="text-neutral-600">
                  Объектов:{' '}
                  <span className="tabular-nums">
                    {layer.object_count.toLocaleString('ru-RU')}
                  </span>
                </span>
                {needsReview ? (
                  <span
                    className="text-amber-700"
                    title={layer.suggestion_reasons?.join('\n')}
                  >
                    проверьте роль
                  </span>
                ) : (
                  mapping.confirmed === true && (
                    <span className="text-green-700">
                      {mapping.kind === 'ignore'
                        ? 'Исключение подтверждено'
                        : 'Роль подтверждена'}
                    </span>
                  )
                )}
              </div>
              {layer.required && (
                <Text
                  variant="caption"
                  className="mt-2 block pl-5 text-blue-700"
                >
                  нужен для границы
                </Text>
              )}
              {!layer.geometry_complete && (
                <Text
                  variant="caption"
                  className="mt-2 block pl-5 text-blue-700"
                >
                  есть замечания к геометрии
                </Text>
              )}
            </div>
            <div className="min-w-0">
              {readOnly ? (
                <div className="text-sm text-neutral-800">
                  {recognition?.categories.find(
                    (item) => item.category === mapping.category,
                  )?.label ??
                    (isUnclassifiedMapping(mapping)
                      ? 'Назначение не определено'
                      : LAYER_KIND_LABELS[mapping.kind])}
                </div>
              ) : (
                <LayerRoleFields
                  layer={layer}
                  mapping={mapping}
                  recognition={recognition}
                  onChange={(next) => {
                    const updated = { ...mappings, [layer.id]: next };
                    onChange(
                      next.kind === 'site_border'
                        ? selectPlanningBoundary(updated, layer.id)
                        : updated,
                    );
                  }}
                />
              )}
            </div>
          </li>
        );
      })}
    </ul>
  </div>
);
