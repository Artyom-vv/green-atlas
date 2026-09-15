import type { FC } from 'react';
import type { SpeciesRevision } from '@green/api-client';
import { Field, TextInput } from '@green/ui';
import { Check, Search } from 'lucide-react';
interface SpeciesCatalogSearchProps {
  compact: boolean;
  query: string;
  selected?: SpeciesRevision;
  showSelection: boolean;
  showResultCount: boolean;
  loading: boolean;
  filteredCount: number;
  speciesCount: number;
  onQueryChange: (value: string) => void;
}
export const SpeciesCatalogSearch: FC<SpeciesCatalogSearchProps> = ({
  compact,
  query,
  selected,
  showSelection,
  showResultCount,
  loading,
  filteredCount,
  speciesCount,
  onQueryChange,
}) => (
  <div className="grid shrink-0 gap-2.5">
    <Field label="Поиск породы">
      <TextInput
        aria-label="Поиск в каталоге пород"
        placeholder="Название или латинское имя"
        startIcon={compact && <Search />}
        value={query}
        onChange={(event) => onQueryChange(event.target.value)}
      />
    </Field>
    {(compact || showSelection) && (
      <div className="flex items-start gap-1.5 text-xs leading-4 text-neutral-700">
        {selected ? (
          <>
            <Check
              className="mt-0.5 shrink-0 text-blue-700"
              size={14}
              aria-hidden="true"
            />
            <span>
              Выбрано: <strong>{selected.common_name}</strong>
            </span>
          </>
        ) : (
          <span>Выберите одну породу для посадок</span>
        )}
      </div>
    )}
    {(compact || showResultCount) && !loading && (
      <div className="flex flex-wrap justify-between gap-x-3 gap-y-1 text-[11px] leading-4 text-neutral-600">
        <span role="status">
          Показано {filteredCount} из {speciesCount}
        </span>
        <span>Размеры взрослого растения</span>
      </div>
    )}
  </div>
);
