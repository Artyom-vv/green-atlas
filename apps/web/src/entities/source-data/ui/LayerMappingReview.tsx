import type { Layer, LayerKind, LayerMapping } from '@green/api-client';
import { Button, DataTable, Disclosure } from '@green/ui';
import type { FC } from 'react';
import { LAYER_KIND_LABELS } from '../model/layerKinds';
import { LayerMappingTable } from './LayerMappingTable';

interface LayerMappingReviewProps {
  layers: Layer[];
  mappings: Record<string, LayerMapping>;
  onChange: (next: Record<string, LayerMapping>) => void;
  readOnly?: boolean;
}

function layerWord(count: number) {
  const mod100 = count % 100;
  const mod10 = count % 10;
  if (mod100 >= 11 && mod100 <= 14) return 'слоёв';
  if (mod10 === 1) return 'слой';
  if (mod10 >= 2 && mod10 <= 4) return 'слоя';
  return 'слоёв';
}

export const LayerMappingReview: FC<LayerMappingReviewProps> = ({
  layers,
  mappings,
  onChange,
  readOnly = false,
}) => {
  const groups = Object.entries(
    layers.reduce<Partial<Record<LayerKind, Layer[]>>>((result, layer) => {
      const kind = mappings[layer.id]?.kind ?? 'ignore';
      (result[kind] ??= []).push(layer);
      return result;
    }, {}),
  ) as [LayerKind, Layer[]][];
  groups.sort(([left], [right]) =>
    LAYER_KIND_LABELS[left].localeCompare(LAYER_KIND_LABELS[right], 'ru'),
  );

  return (
    <section aria-label="Предложенные роли слоёв">
      <DataTable layout="fixed">
        <colgroup>
          <col />
          <col className="w-28" />
          <col className="w-36" />
        </colgroup>
        <thead>
          <tr>
            <th scope="col">Предложенная роль</th>
            <th scope="col">Состав</th>
            <th scope="col" className="text-right">
              Проверка
            </th>
          </tr>
        </thead>
        <tbody>
          {groups.map(([kind, group]) => (
            <tr key={kind} className="bg-yellow-100/50">
              <td className="font-medium">{LAYER_KIND_LABELS[kind]}</td>
              <td className="text-neutral-600">
                {group.length} {layerWord(group.length)}
              </td>
              <td className="text-right">
                {readOnly ? (
                  <span className="text-xs text-amber-700">
                    не подтверждено
                  </span>
                ) : (
                  <Button
                    controlSize="compact"
                    onClick={() => {
                      const next = { ...mappings };
                      for (const layer of group) {
                        const mapping = mappings[layer.id];
                        if (mapping)
                          next[layer.id] = { ...mapping, confirmed: true };
                      }
                      onChange(next);
                    }}
                  >
                    Подтвердить
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </DataTable>
      <Disclosure variant="plain" title="Состав предложений">
        <LayerMappingTable
          readOnly={readOnly}
          layers={layers}
          mappings={mappings}
          onChange={onChange}
        />
      </Disclosure>
    </section>
  );
};
