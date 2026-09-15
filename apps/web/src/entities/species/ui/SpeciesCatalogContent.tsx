import type { FC } from 'react';
import type { SpeciesRevision } from '@green/api-client';
import type { SpeciesCatalogProps } from './SpeciesCatalog';
import { SpeciesCatalogCredits } from './SpeciesCatalogCredits';
import { SpeciesCatalogItem } from './SpeciesCatalogItem';

interface SpeciesCatalogContentProps extends Pick<
  SpeciesCatalogProps,
  | 'value'
  | 'loading'
  | 'loadingMessage'
  | 'disabled'
  | 'itemStatuses'
  | 'onChange'
> {
  filtered: SpeciesRevision[];
  compact: boolean;
  descriptionId: string;
  searching: boolean;
}

export const SpeciesCatalogContent: FC<SpeciesCatalogContentProps> = ({
  filtered,
  compact,
  descriptionId,
  searching,
  loading,
  loadingMessage,
  value,
  disabled = false,
  itemStatuses,
  onChange,
}) => (
  <>
    {loading ? (
      <p role="status">{loadingMessage}</p>
    ) : (
      <div
        className={
          compact
            ? 'grid gap-2'
            : 'grid grid-cols-[repeat(auto-fit,minmax(min(100%,240px),1fr))] gap-3'
        }
      >
        {filtered.map((item, index) => (
          <SpeciesCatalogItem
            key={item.id}
            item={item}
            index={index}
            descriptionId={descriptionId}
            status={itemStatuses?.[item.id]}
            chosen={value === item.id}
            disabled={disabled}
            compact={compact}
            onChange={onChange}
          />
        ))}
      </div>
    )}
    {!loading && !filtered.length && (
      <p role="status" className="text-sm text-neutral-600">
        {searching
          ? 'По этому запросу пород нет. Измените название.'
          : 'Породы для выбора пока недоступны.'}
      </p>
    )}
    <SpeciesCatalogCredits filtered={filtered} />
  </>
);
