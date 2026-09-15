import type { Layer, LayerMapping } from '@green/api-client';
import { DataTable, Select, Text } from '@green/ui';
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
        return (
          <tr
            key={layer.id}
            className={warning ? 'bg-yellow-100/50' : undefined}
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
                      onChange({
                        ...mappings,
                        [layer.id]: { ...mapping, kind },
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
