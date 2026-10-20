/** Исходные данные: общая таблица слоёв, очередь назначений и поиск */
import { useId, useRef, useState } from 'react';
import { Search, X } from 'lucide-react';
import { Button, IconButton, Tabs, TextInput } from '@green/ui';
import type { LayerMappingTableProps } from './LayerMappingTable';
import { LayerMappingTable } from './LayerMappingTable';
import { LayerMappingInformation } from './LayerMappingInformation';
import { requiresLayerRoleReview, toLayerMapping } from '../model/layerKinds';

/** Принятые строки остаются на месте до следующего входа в очередь проверки */
export function LayerMappingWorkspace(
  props: LayerMappingTableProps & {
    automaticAcceptancePending?: boolean;
    informationInHeader?: boolean;
    defaultView?: 'review' | 'list';
  },
) {
  const { layers, mappings, readOnly } = props;
  const id = useId();
  const tabsId = `${id}-tabs`;
  const panelId = `${id}-panel`;
  const [query, setQuery] = useState('');
  const searchInput = useRef<HTMLInputElement>(null);
  const clearSearch = () => {
    setQuery('');
    searchInput.current?.focus();
  };
  const [view, setView] = useState<'review' | 'list'>(
    props.defaultView ?? (readOnly ? 'list' : 'review'),
  );
  const pending = layers.filter((layer) =>
    requiresLayerRoleReview(mappings[layer.id] ?? toLayerMapping(layer)),
  );
  const [reviewIds, setReviewIds] = useState(() => new Set<string>());
  const changeView = (next: 'review' | 'list') => {
    if (next === 'review' && view !== 'review')
      setReviewIds(new Set(pending.map((layer) => layer.id)));
    if (next === 'review') setQuery('');
    setView(next);
  };
  const search = query.trim().toLocaleLowerCase('ru');
  const visible = layers.filter(
    (layer) =>
      (view === 'list' ||
        reviewIds.has(layer.id) ||
        pending.some((item) => item.id === layer.id)) &&
      (view === 'review' ||
        layer.source_name.toLocaleLowerCase('ru').includes(search)),
  );
  if (props.automaticAcceptancePending)
    return (
      <section
        aria-label="Сопоставление слоёв"
        className="min-w-0 px-4 py-4 min-[720px]:px-6"
      >
        <p className="m-0 text-sm text-neutral-600" role="status">
          Принимаем однозначные назначения слоёв
        </p>
      </section>
    );
  return (
    <section aria-label="Сопоставление слоёв" className="min-w-0">
      <div className="flex min-h-10 min-w-0 flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-neutral-200 px-4 min-[720px]:px-6">
        <Tabs
          id={tabsId}
          label="Просмотр слоёв"
          value={view}
          onChange={changeView}
          controlSize="large"
          options={[
            {
              value: 'review',
              label: `Проверить (${pending.length})`,
              panelId,
            },
            { value: 'list', label: `Все слои (${layers.length})`, panelId },
          ]}
        />
        <div className="flex min-w-0 flex-1 items-center justify-end gap-2">
          {!props.informationInHeader && (
            <LayerMappingInformation
              layers={layers}
              mappings={mappings}
              recognition={props.recognition}
            />
          )}
          {view === 'list' && (
            <div className="relative flex w-full max-w-60 min-w-0">
              <TextInput
                ref={searchInput}
                className={query ? 'pr-10' : undefined}
                onKeyDown={(event) => {
                  if (event.key === 'Escape') {
                    event.preventDefault();
                    clearSearch();
                  }
                }}
                variant="ghost"
                controlSize="large"
                startIcon={<Search />}
                aria-label="Поиск слоя"
                placeholder="Найти слой"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              {query && (
                <IconButton
                  icon={X}
                  variant="ghost"
                  controlSize="large"
                  label="Очистить поиск"
                  className="absolute top-0 right-0"
                  onClick={clearSearch}
                />
              )}
            </div>
          )}
        </div>
      </div>
      <div
        id={panelId}
        role="tabpanel"
        aria-labelledby={`${tabsId}-tab-${view}`}
        className="min-w-0"
      >
        <LayerMappingTable
          {...props}
          layers={visible}
          stickyHeader
          onChange={(next) => {
            if (view === 'review')
              setReviewIds(
                (current) =>
                  new Set([...current, ...pending.map((layer) => layer.id)]),
              );
            props.onChange(next);
          }}
        />
        {!visible.length && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 min-[720px]:px-6">
            <p className="m-0 text-sm text-neutral-500" role="status">
              {search
                ? 'Слои не найдены'
                : view === 'review'
                  ? 'Все назначения приняты'
                  : 'В чертеже нет слоёв'}
            </p>
            {(search || view === 'review') && (
              <Button
                variant="ghost"
                controlSize="default"
                className="-ml-3 text-blue-600"
                onClick={() => {
                  if (search) clearSearch();
                  else {
                    changeView('list');
                    document.getElementById(`${tabsId}-tab-list`)?.focus();
                  }
                }}
              >
                {search ? 'Сбросить фильтр' : 'Показать все слои'}
              </Button>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
