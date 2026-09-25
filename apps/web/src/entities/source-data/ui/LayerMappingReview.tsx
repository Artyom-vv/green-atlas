import type { Layer, LayerKind, LayerMapping, LayerRecognition } from '@green/api-client';
import { Button, DataTable, Disclosure } from '@green/ui';
import type { FC } from 'react';
import { changeLayerRole, LAYER_KIND_LABELS, toLayerMapping } from '../model/layerKinds';
import { LayerMappingTable } from './LayerMappingTable';

interface LayerMappingReviewProps {
  layers: Layer[];
  mappings: Record<string, LayerMapping>;
  onChange: (next: Record<string, LayerMapping>) => void;
  readOnly?: boolean;
  recognition?: LayerRecognition;
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
  recognition,
}) => {
  const groups = Object.entries(
    layers.reduce<Record<string, Layer[]>>((result, layer) => {
      const mapping = mappings[layer.id] ?? toLayerMapping(layer);
      const key = mapping.category ? `category:${mapping.category}` : mapping.kind;
      (result[key] ??= []).push(layer);
      return result;
    }, {}),
  );
  const categoryFor = (key: string) => recognition?.categories.find(
    (item) => `category:${item.category}` === key,
  );
  const labelFor = (key: string) => categoryFor(key)?.label ??
    LAYER_KIND_LABELS[key as LayerKind] ?? 'Тип не определён';
  groups.sort(([left], [right]) =>
    labelFor(left).localeCompare(labelFor(right), 'ru'),
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
            <th scope="col">Тип объектов</th>
            <th scope="col">Состав</th>
            <th scope="col" className="text-right">
              Проверка
            </th>
          </tr>
        </thead>
        <tbody>
          {groups.map(([kind, group]) => (
            <tr key={kind} className="bg-yellow-100/50">
              <td className="font-medium">{kind === 'ignore' ? 'Тип не определён' : labelFor(kind)}</td>
              <td className="text-neutral-600">
                {group.length} {layerWord(group.length)}
              </td>
              <td className="text-right">
                {kind === 'ignore' ? (
                  <span className="text-xs text-amber-700">Выберите тип объектов</span>
                ) : readOnly ? (
                  <span className="text-xs text-amber-700">
                    не подтверждено
                  </span>
                ) : (
                  <Button
                    controlSize="compact"
                    onClick={() => {
                      const next = { ...mappings };
                      for (const layer of group) {
                        const mapping = mappings[layer.id] ?? toLayerMapping(layer);
                        const category = categoryFor(kind);
                        next[layer.id] = category
                          ? changeLayerRole(mapping, category.kind ?? 'restricted', category.category)
                          : { ...mapping, confirmed: true };
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
          recognition={recognition}
          readOnly={readOnly}
          layers={layers}
          mappings={mappings}
          onChange={onChange}
        />
      </Disclosure>
    </section>
  );
};
