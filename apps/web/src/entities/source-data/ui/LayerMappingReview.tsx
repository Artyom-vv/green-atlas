import type {
  Layer,
  LayerKind,
  LayerMapping,
  LayerRecognition,
} from '@green/api-client';
import { Button, Disclosure } from '@green/ui';
import type { FC } from 'react';
import {
  changeLayerRole,
  LAYER_KIND_LABELS,
  toLayerMapping,
} from '../model/layerKinds';
import { LayerMappingTable } from './LayerMappingTable';

interface LayerMappingReviewProps {
  layers: Layer[];
  mappings: Record<string, LayerMapping>;
  onChange: (next: Record<string, LayerMapping>) => void;
  readOnly?: boolean;
  recognition?: LayerRecognition;
  showComposition?: boolean;
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
  showComposition = true,
}) => {
  const groups = Object.entries(
    layers.reduce<Record<string, Layer[]>>((result, layer) => {
      const mapping = mappings[layer.id] ?? toLayerMapping(layer);
      const key = mapping.category
        ? `category:${mapping.category}`
        : mapping.kind;
      (result[key] ??= []).push(layer);
      return result;
    }, {}),
  );
  const categoryFor = (key: string) =>
    recognition?.categories.find((item) => `category:${item.category}` === key);
  const labelFor = (key: string) =>
    categoryFor(key)?.label ??
    LAYER_KIND_LABELS[key as LayerKind] ??
    'Тип не определён';
  groups.sort(([left], [right]) =>
    labelFor(left).localeCompare(labelFor(right), 'ru'),
  );

  return (
    <section
      aria-label="Предложенные роли слоёв"
      className="@container grid gap-3"
    >
      <ul className="m-0 grid list-none gap-2 p-0">
        {groups.map(([kind, group]) => (
          <li
            key={kind}
            className="grid min-w-0 gap-3 rounded-lg bg-neutral-50 p-3 @lg:grid-cols-[minmax(0,1fr)_10rem] @lg:items-center"
          >
            <div className="min-w-0">
              <div className="text-sm font-medium wrap-anywhere">
                {kind === 'ignore' ? 'Тип не определён' : labelFor(kind)}
              </div>
              <div className="mt-1 text-xs text-neutral-600">
                {group.length} {layerWord(group.length)}
              </div>
            </div>
            <div className="min-w-0 @lg:justify-self-end">
              {kind === 'site_border' ||
              categoryFor(kind)?.kind === 'site_border' ? (
                <span className="text-xs text-neutral-600">
                  Выберите один контур в разделе «Территория»
                </span>
              ) : kind === 'ignore' ? (
                <span className="text-xs text-amber-700">
                  Выберите тип объектов
                </span>
              ) : readOnly ? (
                <span className="text-xs text-amber-700">не подтверждено</span>
              ) : (
                <Button
                  controlSize="compact"
                  onClick={() => {
                    const next = { ...mappings };
                    for (const layer of group) {
                      const mapping =
                        mappings[layer.id] ?? toLayerMapping(layer);
                      const category = categoryFor(kind);
                      next[layer.id] = category
                        ? changeLayerRole(
                            mapping,
                            category.kind ?? 'restricted',
                            category.category,
                          )
                        : { ...mapping, confirmed: true };
                    }
                    onChange(next);
                  }}
                >
                  Подтвердить
                </Button>
              )}
            </div>
          </li>
        ))}
      </ul>
      {showComposition && (
        <Disclosure variant="panel" title="Состав предложений">
          <LayerMappingTable
            recognition={recognition}
            readOnly={readOnly}
            layers={layers}
            mappings={mappings}
            onChange={onChange}
          />
        </Disclosure>
      )}
    </section>
  );
};
