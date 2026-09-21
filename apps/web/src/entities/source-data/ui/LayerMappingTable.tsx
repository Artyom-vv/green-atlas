import type { Layer, LayerMapping } from '@green/api-client';
import { Button, DataTable, Select, Text } from '@green/ui';
import type { CSSProperties, FC } from 'react';
import {
  LAYER_KIND_LABELS,
  LAYER_KIND_OPTIONS,
  layerKindFromValue,
  toLayerMapping,
} from '../model/layerKinds';

export interface LayerMappingTableProps {
  layers: Layer[];
  mappings: Record<string, LayerMapping>;
  onChange: (next: Record<string, LayerMapping>) => void;
  readOnly?: boolean;
}

export const LayerMappingTable: FC<LayerMappingTableProps> = ({
  layers,
  mappings,
  onChange,
  readOnly = false,
}) => (
  <DataTable layout="fixed" className="min-w-144">
    <colgroup>
      <col />
      <col className="w-60" />
      <col className="w-24" />
    </colgroup>
    <thead>
      <tr>
        <th scope="col">Слой DXF</th>
        <th scope="col">Использовать как</th>
        <th scope="col" className="text-right">
          Объектов
        </th>
      </tr>
    </thead>
    <tbody>
      {layers.map((layer) => {
        const mapping = mappings[layer.id] ?? toLayerMapping(layer);
        const warning =
          (mapping.kind === 'ignore' && layer.required) ||
          !layer.geometry_complete;
        const needsReview = Boolean(
          layer.mapping_review_required &&
          mapping.kind !== 'ignore' &&
          !mapping.confirmed,
        );
        const reviewReason = layer.suggestion_reasons?.join('. ');
        return (
          <tr
            key={layer.id}
            className={warning || needsReview ? 'bg-yellow-100/50' : undefined}
          >
            <td>
              <div className="grid grid-cols-[12px_minmax(100px,1fr)] items-center gap-x-2 gap-y-1">
                <span
                  aria-hidden="true"
                  className="size-3 border-2 border-(--layer-color)"
                  style={{ '--layer-color': layer.color } as CSSProperties}
                />
                <code className="font-mono text-xs wrap-anywhere text-neutral-700">
                  {layer.source_name}
                </code>
                {!!layer.required && (
                  <Text variant="caption" className="col-start-2 text-blue-700">
                    нужен для границы
                  </Text>
                )}
                {!layer.geometry_complete && (
                  <Text variant="caption" className="col-start-2 text-blue-700">
                    часть объектов не показана
                  </Text>
                )}
                {needsReview && (
                  <Text
                    variant="caption"
                    className="col-start-2 text-amber-700"
                    title={reviewReason}
                  >
                    проверьте роль
                  </Text>
                )}
              </div>
            </td>
            <td>
              {readOnly ? (
                LAYER_KIND_LABELS[mapping.kind]
              ) : (
                <Select
                  aria-label={`Тип слоя ${layer.source_name}`}
                  value={mapping.kind}
                  onChange={(event) => {
                    const kind = layerKindFromValue(event.target.value);
                    if (kind) {
                      const nextMapping = { ...mapping, kind };
                      if (layer.mapping_review_required)
                        nextMapping.confirmed = true;
                      onChange({
                        ...mappings,
                        [layer.id]: nextMapping,
                      });
                    }
                  }}
                >
                  {LAYER_KIND_OPTIONS.map((option) => (
                    <option value={option.value} key={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
              )}
              {!readOnly && needsReview && (
                <Button
                  controlSize="compact"
                  variant="ghost"
                  className="mt-1"
                  onClick={() =>
                    onChange({
                      ...mappings,
                      [layer.id]: { ...mapping, confirmed: true },
                    })
                  }
                >
                  Подтвердить
                </Button>
              )}
            </td>
            <td className="text-right font-mono tabular-nums">
              {layer.object_count}
            </td>
          </tr>
        );
      })}
    </tbody>
  </DataTable>
);
