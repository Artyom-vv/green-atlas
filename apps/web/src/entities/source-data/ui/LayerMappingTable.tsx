import type { Layer, LayerMapping, LayerRecognition } from '@green/api-client';
import { DataTable, Text } from '@green/ui';
import type { CSSProperties, FC } from 'react';
import {
  LAYER_KIND_LABELS,
  isUnclassifiedMapping,
  toLayerMapping,
} from '../model/layerKinds';
import { LayerRoleFields } from './LayerRoleFields';

export interface LayerMappingTableProps {
  layers: Layer[];
  mappings: Record<string, LayerMapping>;
  onChange: (next: Record<string, LayerMapping>) => void;
  readOnly?: boolean;
  recognition?: LayerRecognition;
}

export const LayerMappingTable: FC<LayerMappingTableProps> = ({
  layers,
  mappings,
  onChange,
  readOnly = false,
  recognition,
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
        <th scope="col">Тип объектов</th>
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
          mapping.confirmed === false,
        );
        const reviewReason = layer.suggestion_reasons?.join('\n');
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
                    есть замечания к геометрии
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
                <>
                  <div>{recognition?.categories.find((item) => item.category === mapping.category)?.label ??
                    (isUnclassifiedMapping(mapping) ? 'Назначение не определено' : LAYER_KIND_LABELS[mapping.kind])}</div>
                </>
              ) : (
                <LayerRoleFields layer={layer} mapping={mapping} recognition={recognition}
                  onChange={(next) => onChange({ ...mappings, [layer.id]: next })} />
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
