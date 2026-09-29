import { useState } from 'react';
import { Button, TextInput, Text } from '@green/ui';
import type { LayerMappingTableProps } from './LayerMappingTable';
import { LayerMappingTable } from './LayerMappingTable';
import { LayerMappingReview } from './LayerMappingReview';
import { toLayerMapping } from '../model/layerKinds';

/** One stable list: confirming a row must not move it under the user's cursor. */
export function LayerMappingWorkspace(
  props: LayerMappingTableProps & {
    automaticAcceptancePending?: boolean;
  },
) {
  const { layers, mappings, readOnly } = props;
  const [query, setQuery] = useState('');
  const pending = layers.filter(
    (layer) =>
      (mappings[layer.id] ?? toLayerMapping(layer)).confirmed === false,
  );
  const proposalsById = new Map(
    props.recognition?.proposals.map((item) => [item.layer_id, item]) ?? [],
  );
  const categoriesById = new Map(
    props.recognition?.categories.map((item) => [item.category, item]) ?? [],
  );
  const reviewReasons = pending.reduce(
    (counts, layer) => {
      const proposal = proposalsById.get(layer.id);
      const category = proposal?.category
        ? categoriesById.get(proposal.category)
        : undefined;
      if (category?.kind === 'site_border') counts.boundary += 1;
      else if (category && category.kind == null) counts.descriptive += 1;
      else counts.uncertain += 1;
      return counts;
    },
    { boundary: 0, descriptive: 0, uncertain: 0 },
  );
  const confirmedCount = layers.length - pending.length;
  const [view, setView] = useState<'review' | 'list'>('review');
  const showReview = !readOnly && view === 'review';
  const search = query.trim().toLocaleLowerCase('ru');
  const visible = layers.filter((layer) =>
    layer.source_name.toLocaleLowerCase('ru').includes(search),
  );
  if (props.automaticAcceptancePending)
    return (
      <section aria-label="Сопоставление слоёв" className="min-w-0">
        <p className="m-0 text-sm text-neutral-600" role="status">
          Принимаем однозначные назначения слоёв
        </p>
      </section>
    );
  return (
    <section
      aria-label="Сопоставление слоёв"
      className="@container grid min-w-0 gap-3"
    >
      {props.recognition?.status === 'completed' && (
        <div
          className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm text-neutral-600"
          role="status"
        >
          <span>
            Распознано Luna: {props.recognition.processed_count} из{' '}
            {layers.length}
          </span>
          <span>
            Подтверждено: {confirmedCount} из {layers.length}
          </span>
          {pending.length > 0 && <span>Проверить: {pending.length}</span>}
          {pending.length > 0 && (
            <details className="basis-full text-neutral-600">
              <summary className="w-fit cursor-pointer">
                Почему нужна проверка
              </summary>
              <p className="mt-2 mb-0">
                Границы: {reviewReasons.boundary}, без расчётной роли:{' '}
                {reviewReasons.descriptive}, другие: {reviewReasons.uncertain}
              </p>
            </details>
          )}
        </div>
      )}
      {!readOnly && (
        <div
          role="group"
          aria-label="Просмотр слоёв"
          className="flex flex-wrap gap-1"
        >
          <Button
            variant={showReview ? 'secondary' : 'ghost'}
            aria-pressed={showReview}
            onClick={() => setView('review')}
          >
            Подтверждение ({pending.length})
          </Button>
          <Button
            variant={!showReview ? 'secondary' : 'ghost'}
            aria-pressed={!showReview}
            onClick={() => setView('list')}
          >
            Все слои ({layers.length})
          </Button>
        </div>
      )}
      {showReview ? (
        <section aria-label="Подтверждение групп" className="grid gap-2">
          {!pending.length && (
            <p className="m-0 text-sm">Все типы слоёв подтверждены</p>
          )}
          <LayerMappingReview
            {...props}
            layers={pending}
            showComposition={false}
          />
        </section>
      ) : (
        <>
          <div className="grid min-w-0 items-center gap-2">
            <TextInput
              aria-label="Поиск слоя"
              placeholder="Найти слой по названию"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <Text variant="caption" className="text-neutral-600" role="status">
            Показано {visible.length} из {layers.length}
          </Text>
          <div
            className="max-h-[65vh] overflow-auto border border-neutral-200"
          >
            <LayerMappingTable {...props} layers={visible} />
            {!visible.length && (
              <p className="px-4 text-sm text-neutral-600">Слои не найдены</p>
            )}
          </div>
        </>
      )}
    </section>
  );
}
