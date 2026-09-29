import type {
  Layer,
  LayerKind,
  LayerMapping,
  LayerRecognition,
} from '@green/api-client';
import { Button, Disclosure } from '@green/ui';
import { useState, type FC } from 'react';
import {
  changeLayerRole,
  LAYER_KIND_LABELS,
  toLayerMapping,
} from '../model/layerKinds';
import { localLayerName } from '../model/boundaryDecision';
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
  const [activeIndex, setActiveIndex] = useState(0);
  const proposedMapping = (layer: Layer) => {
    const mapping = mappings[layer.id] ?? toLayerMapping(layer);
    if (mapping.confirmed !== false) return mapping;
    const proposal = recognition?.proposals.find(
      (item) => item.layer_id === layer.id,
    );
    const category = recognition?.categories.find(
      (item) => item.category === proposal?.category,
    );
    if (!category || proposal?.confidence === 'low') return mapping;
    // A descriptive category is not a calculation role.
    return {
      ...mapping,
      kind: category.kind ?? mapping.kind,
      category: category.category,
    };
  };
  const groups = Object.entries(
    layers.reduce<Record<string, Layer[]>>((result, layer) => {
      const mapping = proposedMapping(layer);
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
  const currentIndex = Math.min(activeIndex, Math.max(groups.length - 1, 0));
  const visibleGroups = groups.length ? [groups[currentIndex]] : [];

  return (
    <section
      aria-label="Предложенные роли слоёв"
      className="@container grid gap-3"
    >
      {groups.length > 1 && (
        <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
          <span className="text-neutral-600">
            Группа {currentIndex + 1} из {groups.length}
          </span>
          <div className="flex gap-2">
            <Button
              variant="ghost"
              controlSize="compact"
              disabled={currentIndex === 0}
              onClick={() => setActiveIndex(currentIndex - 1)}
            >
              Назад
            </Button>
            <Button
              variant="ghost"
              controlSize="compact"
              disabled={currentIndex === groups.length - 1}
              onClick={() => setActiveIndex(currentIndex + 1)}
            >
              Далее
            </Button>
          </div>
        </div>
      )}
      <ul className="m-0 list-none border-y border-neutral-200 p-0">
        {visibleGroups.map(([kind, group]) => (
          <li key={kind} className="min-w-0 bg-white px-4 py-4">
            <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-sm font-semibold wrap-anywhere">
                  {kind === 'ignore' ? 'Тип не определён' : labelFor(kind)}
                </div>
                <div className="mt-1 text-xs text-neutral-600">
                  {group.length === 1
                    ? `${localLayerName(group[0])}, объектов: ${group[0].object_count.toLocaleString('ru-RU')}`
                    : `${group.length} ${layerWord(group.length)}`}
                </div>
              </div>
              <div className="min-w-0 max-w-md text-sm text-neutral-600">
                {kind === 'site_border' ||
                categoryFor(kind)?.kind === 'site_border' ? (
                  <span className="text-neutral-600">
                    Укажите границу работ в разделе «Территория»
                  </span>
                ) : categoryFor(kind)?.kind == null && categoryFor(kind) ? (
                  <span className="text-neutral-600">
                    Расчётная роль не установлена
                  </span>
                ) : kind === 'ignore' ? (
                  <span className="text-amber-700">
                    Не удалось определить тип
                  </span>
                ) : readOnly ? (
                  <span className="text-amber-700">Не подтверждено</span>
                ) : (
                  <Button
                    controlSize="compact"
                    onClick={() => {
                      const next = { ...mappings };
                      for (const layer of group) {
                        const mapping =
                          mappings[layer.id] ?? toLayerMapping(layer);
                        if (mapping.confirmed === true) continue;
                        const category = categoryFor(kind);
                        if (category?.kind == null && category) continue;
                        next[layer.id] = category
                          ? changeLayerRole(
                              mapping,
                              category.kind,
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
            </div>
            {categoryFor(kind)?.kind == null && categoryFor(kind) && (
              <p className="mb-0 mt-2 max-w-2xl text-xs leading-5 text-neutral-600">
                Тип распознан, но влияние на посадки не следует из названия
                слоя. Ошибочное ограничение может скрыть пригодную площадь.
              </p>
            )}
            <details className="mt-2 text-sm">
              <summary className="w-fit cursor-pointer py-1 text-blue-700">
                {group.length === 1
                  ? 'Посмотреть слой и решение'
                  : `Просмотреть слои (${group.length})`}
              </summary>
              <div className="mt-2 border border-solid border-neutral-200">
                <LayerMappingTable
                  recognition={recognition}
                  readOnly={readOnly}
                  layers={group}
                  mappings={mappings}
                  onChange={onChange}
                />
              </div>
            </details>
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
