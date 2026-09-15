import { useId, useState, type FC } from 'react';
import type { SpeciesRevision } from '@green/api-client';
import { ScrollArea, cx } from '@green/ui';
import { SpeciesCatalogContent } from './SpeciesCatalogContent';
import { SpeciesCatalogSearch } from './SpeciesCatalogSearch';
export interface SpeciesCatalogProps {
  species: SpeciesRevision[];
  value?: string;
  onChange: (id: string) => void;
  disabled?: boolean;
  loading?: boolean;
  variant?: 'catalog' | 'placement';
  /** Card presentation is independent of whether the parent supplies height. */
  layout?: 'document' | 'fill';
  query?: string;
  onQueryChange?: (query: string) => void;
  showResultCount?: boolean;
  showSelection?: boolean;
  itemStatuses?: Readonly<
    Record<string, { label: string; tone: 'neutral' | 'warning' }>
  >;
  loadingMessage?: string;
}

export const SpeciesCatalog: FC<SpeciesCatalogProps> = ({
  species,
  value,
  onChange,
  disabled = false,
  loading = false,
  variant = 'catalog',
  layout = 'document',
  query: controlledQuery,
  onQueryChange,
  showResultCount = false,
  showSelection = false,
  itemStatuses,
  loadingMessage = 'Загружаем породы для выбранных участков',
}) => {
  const [localQuery, setLocalQuery] = useState('');
  const query = controlledQuery ?? localQuery;
  const descriptionId = useId();
  const compact = variant === 'placement';
  const selected = species.find((item) => item.id === value);
  const filtered = species.filter((item) =>
    `${item.common_name} ${item.scientific_name}`
      .toLocaleLowerCase('ru')
      .includes(query.toLocaleLowerCase('ru').trim()),
  );
  const content = (
    <SpeciesCatalogContent
      filtered={filtered}
      compact={compact}
      descriptionId={descriptionId}
      searching={Boolean(query.trim())}
      loading={loading}
      loadingMessage={loadingMessage}
      value={value}
      disabled={disabled}
      itemStatuses={itemStatuses}
      onChange={onChange}
    />
  );
  return (
    <div
      className={cx(
        'min-w-0 gap-4',
        layout === 'fill' ? 'flex min-h-0 flex-1 flex-col' : 'grid',
      )}
    >
      <SpeciesCatalogSearch
        compact={compact}
        query={query}
        selected={selected}
        showSelection={showSelection}
        showResultCount={showResultCount}
        loading={loading}
        filteredCount={filtered.length}
        speciesCount={species.length}
        onQueryChange={(value) => {
          if (controlledQuery === undefined) setLocalQuery(value);
          onQueryChange?.(value);
        }}
      />
      {layout === 'fill' ? (
        <ScrollArea className="flex-1" contentClassName="p-1">
          {content}
        </ScrollArea>
      ) : (
        <div className="min-w-0">{content}</div>
      )}
    </div>
  );
};
