import { useLayoutEffect, useRef, useState, type FC } from 'react';
import type { SpeciesRevision } from '@green/api-client';
import { Button, ScrollArea, TextInput, cx } from '@green/ui';
import { ArrowLeft, Check, Search } from 'lucide-react';
import { usePlantCatalog } from '../model/PlantCatalogContext';
import { SpeciesInspector } from './SpeciesInspector';
import { SpeciesSummary } from './SpeciesSummary';

export interface SpeciesCatalogProps {
  species: SpeciesRevision[];
  value?: string;
  onChange: (id: string) => void;
  disabled?: boolean;
  loading?: boolean;
  layout?: 'document' | 'fill';
  query?: string;
  onQueryChange?: (query: string) => void;
  showResultCount?: boolean;
  showSelection?: boolean;
  itemStatuses?: Readonly<
    Record<
      string,
      { label: string; tone: 'neutral' | 'warning'; canSelect?: boolean }
    >
  >;
  loadingMessage?: string;
}

export const SpeciesCatalog: FC<SpeciesCatalogProps> = ({
  species,
  value,
  onChange,
  disabled,
  loading,
  layout = 'document',
  query: controlledQuery,
  onQueryChange,
  showSelection,
  itemStatuses,
  loadingMessage = 'Загружаем растения',
}) => {
  const library = usePlantCatalog();
  const [localQuery, setQuery] = useState('');
  const [focused, setFocused] = useState<string>();
  const [all, setAll] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const detailsViewport = useRef<HTMLDivElement>(null);
  const listViewport = useRef<HTMLDivElement>(null);
  const detailsHeading = useRef<HTMLHeadingElement>(null);
  const returnToList = useRef(false);
  const query = controlledQuery ?? localQuery;
  const kinds = new Set(species.map((s) => s.kind));
  const singleKind = kinds.size === 1 ? species[0]?.kind : undefined;
  const inventory = library.inventory;
  const entries =
    all && inventory
      ? inventory.entries
          .filter((e) => !singleKind || e.kind === singleKind)
          .map((entry) => ({
            id: entry.id,
            name: entry.name,
            kind: entry.kind,
            entry,
            profile: species.find(
              (s) => s.species_id === entry.calculation_species_id,
            ),
          }))
      : species.map((profile) => ({
          id: profile.id,
          name: profile.common_name,
          kind: profile.kind,
          profile,
          entry: inventory?.entries.find(
            (e) => e.calculation_species_id === profile.species_id,
          ),
        }));
  const filtered = entries.filter((e) =>
    `${e.name} ${e.profile?.scientific_name ?? ''}`
      .toLocaleLowerCase('ru')
      .includes(query.trim().toLocaleLowerCase('ru')),
  );
  const selected =
    filtered.find((e) => e.id === focused) ??
    filtered.find((e) => e.profile?.id === value) ??
    filtered[0];
  const status = selected?.profile
    ? itemStatuses?.[selected.profile.id]
    : undefined;
  const canSelect = Boolean(
    selected?.profile && status?.canSelect !== false && !disabled && !loading,
  );
  useLayoutEffect(() => {
    if (detailsViewport.current) detailsViewport.current.scrollTop = 0;
  }, [selected?.id]);
  useLayoutEffect(() => {
    if (detailsOpen) detailsHeading.current?.focus({ preventScroll: true });
    else if (returnToList.current) {
      listViewport.current
        ?.querySelector<HTMLElement>('[aria-pressed="true"]')
        ?.focus();
      returnToList.current = false;
    }
  }, [detailsOpen, selected?.id]);
  useLayoutEffect(() => {
    const viewport = listViewport.current;
    const current = viewport?.querySelector<HTMLElement>(
      '[aria-current="true"]',
    );
    if (viewport && current) {
      viewport.scrollTop +=
        current.getBoundingClientRect().top -
        viewport.getBoundingClientRect().top;
    }
  }, [value, all, loading]);
  return (
    <div
      className={cx(
        '@container/catalog flex min-h-0 min-w-0 flex-col gap-3',
        layout === 'fill' ? 'flex-1' : 'h-[min(640px,70vh)]',
      )}
    >
      <div className="grid shrink-0 gap-3">
        <TextInput
          aria-label="Поиск в каталоге пород"
          placeholder="Найти растение"
          startIcon={<Search />}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setDetailsOpen(false);
            onQueryChange?.(e.target.value);
          }}
        />
        <div
          className="flex flex-wrap items-center gap-1"
          aria-label="Состав каталога"
        >
          <Button
            variant={all ? 'ghost' : 'secondary'}
            aria-pressed={!all}
            onClick={() => setAll(false)}
          >
            Для расчёта ({species.length})
          </Button>
          {inventory && (
            <Button
              variant={all ? 'secondary' : 'ghost'}
              aria-pressed={all}
              onClick={() => setAll(true)}
            >
              Весь ассортимент (
              {
                inventory.entries.filter(
                  (e) => !singleKind || e.kind === singleKind,
                ).length
              }
              )
            </Button>
          )}
          <span className="ml-auto text-xs text-neutral-600" role="status">
            {loading ? loadingMessage : `Найдено ${filtered.length}`}
          </span>
        </div>
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden rounded-md border border-neutral-200 @min-[640px]/catalog:grid-cols-[minmax(280px,1fr)_minmax(0,1.1fr)]">
        <ScrollArea
          className={cx(
            'min-h-0 @min-[640px]/catalog:block',
            detailsOpen && 'hidden',
          )}
          viewportProps={{ ref: listViewport, 'aria-label': 'Список растений' }}
          contentClassName="p-1"
        >
          {loading ? (
            <p className="p-3 text-xs">{loadingMessage}</p>
          ) : (
            filtered.map((item) => {
              const itemStatus =
                item.profile && itemStatuses?.[item.profile.id];
              return (
                <Button
                  key={item.id}
                  variant="ghost"
                  className={cx(
                    'mb-0.5 h-auto w-full justify-start gap-3 px-3 py-3 text-left whitespace-normal',
                    selected?.id === item.id && 'bg-blue-100 hover:bg-blue-100',
                  )}
                  aria-pressed={selected?.id === item.id}
                  aria-current={item.profile?.id === value ? 'true' : undefined}
                  aria-label={`Сведения: ${item.name}`}
                  aria-description={[
                    item.profile?.scientific_name,
                    item.profile && itemStatuses?.[item.profile.id]?.label,
                  ]
                    .filter(Boolean)
                    .join(' ')}
                  onClick={() => {
                    setFocused(item.id);
                    setDetailsOpen(true);
                  }}
                  endIcon={item.profile?.id === value ? <Check /> : undefined}
                  content={
                    <span className="grid min-w-0 flex-1 gap-1">
                      {item.profile ? (
                        <SpeciesSummary species={item.profile} />
                      ) : (
                        <>
                          <strong className="text-sm font-medium">
                            {item.name}
                          </strong>
                          <span className="text-xs text-neutral-600">
                            Без расчётного профиля
                          </span>
                        </>
                      )}
                      {itemStatus && (
                        <span
                          className={cx(
                            'text-xs leading-4',
                            itemStatus.tone === 'warning'
                              ? 'text-amber-700'
                              : 'text-neutral-600',
                          )}
                        >
                          {itemStatus.label}
                        </span>
                      )}
                    </span>
                  }
                />
              );
            })
          )}
          {!loading && !filtered.length && (
            <p className="p-3 text-xs text-neutral-600">
              {entries.length
                ? 'По этому запросу пород нет. Измените название.'
                : 'Породы для выбора пока недоступны.'}
            </p>
          )}
        </ScrollArea>
        <section
          aria-label="Сведения о растении"
          className={cx(
            'min-h-0 flex-col @min-[640px]/catalog:flex @min-[640px]/catalog:border-l @min-[640px]/catalog:border-neutral-200',
            detailsOpen ? 'flex' : 'hidden',
          )}
        >
          <div className="grid shrink-0 gap-2 border-b border-neutral-200 p-4">
            <Button
              variant="ghost"
              className="justify-self-start @min-[640px]/catalog:hidden"
              startIcon={<ArrowLeft />}
              onClick={() => {
                returnToList.current = true;
                setDetailsOpen(false);
              }}
            >
              К списку растений
            </Button>
            <h3
              ref={detailsHeading}
              tabIndex={-1}
              className="m-0 text-base font-semibold outline-offset-2 focus-visible:outline-2 focus-visible:outline-blue-600"
            >
              {selected?.name ?? 'Выберите растение'}
            </h3>
            {selected?.profile && (
              <p className="m-0 text-xs text-neutral-600 italic">
                {selected.profile.scientific_name}
              </p>
            )}
          </div>
          <ScrollArea
            className="min-h-0 flex-1"
            viewportProps={{
              ref: detailsViewport,
              'aria-label': 'Характеристики растения',
            }}
          >
            <SpeciesInspector
              showHeading={false}
              species={selected?.profile}
              entry={selected?.entry}
              inventory={inventory}
              reason={status?.label}
              reasonTone={status?.tone}
            />
          </ScrollArea>
        </section>
      </div>
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-neutral-200 pt-3">
        <span className="min-w-0 text-xs text-neutral-600">
          {selected?.profile
            ? selected.profile.common_name
            : selected
              ? 'Для этого вида пока нет расчётного профиля'
              : showSelection && value
                ? species.find((s) => s.id === value)?.common_name
                : 'Выберите растение'}
        </span>
        <Button
          variant="primary"
          disabled={!canSelect}
          onClick={() => {
            if (canSelect && selected?.profile) onChange(selected.profile.id);
          }}
        >
          Выбрать растение
        </Button>
      </div>
    </div>
  );
};
