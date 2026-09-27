import { useState, type FC } from 'react';
import { ControlProvider, InlineMessage } from '@green/ui';
import { repeatedItemLabel } from '@/entities/planting-zone/model/plantingZoneLabels';
import type {
  PlantingZoneManagerProps,
  ZoneFilter,
} from './PlantingZoneManager.types';
import { ZoneManagerToolbar } from './ZoneManagerToolbar';
import { ZoneSelectionBar } from './ZoneSelectionBar';
import { ZoneTable } from './ZoneTable';
export type { PlantingZoneManagerProps } from './PlantingZoneManager.types';
export const PlantingZoneManager: FC<PlantingZoneManagerProps> = ({
  zones,
  activeId,
  activeIds = [],
  onSelectionChange,
  zoneUsage = {},
  drawing = false,
  saving = false,
  error,
  onFocus,
  onRename,
  onRedraw,
  onDelete,
  onDraw,
  onCancelDraw,
}) => {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<ZoneFilter>('all');
  const visible = zones.filter(
    (zone) =>
      repeatedItemLabel(zones, zone)
        .toLocaleLowerCase('ru')
        .includes(query.toLocaleLowerCase('ru').trim()) &&
      (filter !== 'selected' || activeIds.includes(zone.id!)) &&
      (filter !== 'empty' || !zoneUsage[zone.id!]),
  );
  return (
    <ControlProvider size="compact">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 text-xs">
        <ZoneManagerToolbar
          query={query}
          filter={filter}
          setQuery={setQuery}
          setFilter={setFilter}
          drawing={drawing}
          saving={saving}
          onDraw={onDraw}
          onCancelDraw={onCancelDraw}
        />
        <ZoneSelectionBar
          zones={zones}
          activeIds={activeIds}
          onSelectionChange={onSelectionChange}
          saving={saving}
          visibleCount={visible.length}
        />
        <ZoneTable
          zones={zones}
          visible={visible}
          activeId={activeId}
          activeIds={activeIds}
          zoneUsage={zoneUsage}
          saving={saving}
          onSelectionChange={onSelectionChange}
          onFocus={onFocus}
          onRename={onRename}
          onRedraw={onRedraw}
          onDelete={onDelete}
        />
        {drawing && (
          <InlineMessage tone="info">
            Поставьте точки и замкните контур на карте.
          </InlineMessage>
        )}
        {error && <InlineMessage tone="error">{error}</InlineMessage>}
      </div>
    </ControlProvider>
  );
};
