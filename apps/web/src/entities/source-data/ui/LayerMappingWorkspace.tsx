import { useRef, useState } from 'react';
import { Button, Disclosure, TextInput, Text } from '@green/ui';
import type { LayerMappingTableProps } from './LayerMappingTable';
import { LayerMappingTable } from './LayerMappingTable';
import { LayerMappingReview } from './LayerMappingReview';
import { toLayerMapping } from '../model/layerKinds';

/** One stable list: confirming a row must not move it under the user's cursor. */
export function LayerMappingWorkspace(props: LayerMappingTableProps) {
  const { layers, mappings, readOnly } = props;
  const [query, setQuery] = useState('');
  const [reviewIds, setReviewIds] = useState<string[] | null>(null);
  const table = useRef<HTMLDivElement>(null);
  const pending = layers.filter(
    (layer) =>
      (mappings[layer.id] ?? toLayerMapping(layer)).confirmed === false,
  );
  const search = query.trim().toLocaleLowerCase('ru');
  const visible = layers.filter(
    (layer) =>
      (reviewIds === null || reviewIds.includes(layer.id)) &&
      layer.source_name.toLocaleLowerCase('ru').includes(search),
  );
  return (
    <section
      aria-label="Сопоставление слоёв"
      className="@container grid min-w-0 gap-3"
    >
      {!readOnly && pending.length > 0 && (
        <Disclosure
          variant="panel"
          title={`Предложения по группам (${pending.length})`}
          description="Подтвердить одинаковые типы сразу для нескольких слоёв"
        >
          <LayerMappingReview
            {...props}
            layers={pending}
            showComposition={false}
          />
        </Disclosure>
      )}
      <div className="grid min-w-0 items-center gap-2 @2xl:grid-cols-[minmax(0,1fr)_auto]">
        <TextInput
          aria-label="Поиск слоя"
          placeholder="Найти слой по названию"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        {!readOnly && (
          <div className="flex flex-wrap gap-1">
            <Button
              variant={reviewIds === null ? 'secondary' : 'ghost'}
              aria-pressed={reviewIds === null}
              onClick={() => setReviewIds(null)}
            >
              Все
            </Button>
            <Button
              variant={reviewIds !== null ? 'secondary' : 'ghost'}
              aria-pressed={reviewIds !== null}
              onClick={() => setReviewIds(pending.map((layer) => layer.id))}
            >
              Требуют проверки ({pending.length})
            </Button>
          </div>
        )}
      </div>
      <Text variant="caption" className="text-neutral-600" role="status">
        Показано {visible.length} из {layers.length}.
        {!readOnly && ' Alt + ↑/↓ — переход между слоями.'}
        {reviewIds !== null &&
          ' Подтверждённые строки остаются на месте; нажмите фильтр ещё раз, чтобы обновить список.'}
      </Text>
      <div
        ref={table}
        className="max-h-[65vh] overflow-auto rounded-xl border border-neutral-200"
        onKeyDown={(event) => {
          if (!event.altKey || !['ArrowDown', 'ArrowUp'].includes(event.key))
            return;
          const controls = Array.from(
            table.current?.querySelectorAll<HTMLSelectElement>(
              'select[aria-label^="Тип слоя"]:not(:disabled)',
            ) ?? [],
          );
          const current = controls.indexOf(event.target as HTMLSelectElement);
          if (current < 0) return;
          const next = controls[current + (event.key === 'ArrowDown' ? 1 : -1)];
          if (next) {
            event.preventDefault();
            next.focus();
          }
        }}
      >
        <LayerMappingTable {...props} layers={visible} />
        {!visible.length && (
          <p className="px-4 text-sm text-neutral-600">Слои не найдены</p>
        )}
      </div>
    </section>
  );
}
