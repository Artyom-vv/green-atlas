import type { FC } from 'react';
import { Plus, Search } from 'lucide-react';
import { Button, FieldGrid, Select, TextInput } from '@green/ui';
import type {
  PlantingZoneManagerProps,
  ZoneFilter,
} from './PlantingZoneManager.types';
interface ZoneManagerToolbarProps extends Pick<
  PlantingZoneManagerProps,
  'drawing' | 'saving' | 'onDraw' | 'onCancelDraw'
> {
  query: string;
  filter: ZoneFilter;
  setQuery: (value: string) => void;
  setFilter: (value: ZoneFilter) => void;
}
export const ZoneManagerToolbar: FC<ZoneManagerToolbarProps> = ({
  query,
  filter,
  setQuery,
  setFilter,
  drawing,
  saving,
  onDraw,
  onCancelDraw,
}) => (
  <FieldGrid minWidth={160} className="shrink-0 items-end gap-3">
    <TextInput
      startIcon={<Search />}
      aria-label="Поиск участков"
      placeholder="Найти участок…"
      value={query}
      onChange={(event) => setQuery(event.target.value)}
    />
    <Select
      aria-label="Фильтр участков"
      value={filter}
      onChange={(event) => setFilter(event.target.value as typeof filter)}
    >
      <option value="all">Все участки</option>
      <option value="selected">Выбранные</option>
      <option value="empty">Без посадок</option>
    </Select>
    <Button
      variant={drawing ? 'secondary' : 'primary'}
      disabled={saving}
      onClick={drawing ? onCancelDraw : onDraw}
      icon={drawing ? undefined : Plus}
    >
      {drawing ? 'Отменить обводку' : 'Новый участок'}
    </Button>
  </FieldGrid>
);
