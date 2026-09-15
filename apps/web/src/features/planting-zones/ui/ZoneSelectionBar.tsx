import type { FC } from 'react';
import { Button, FormActions } from '@green/ui';
import type { PlantingZoneManagerProps } from './PlantingZoneManager.types';
interface ZoneSelectionBarProps extends Pick<
  PlantingZoneManagerProps,
  'zones' | 'onSelectionChange' | 'saving'
> {
  activeIds: string[];
  visibleCount: number;
}
export const ZoneSelectionBar: FC<ZoneSelectionBarProps> = ({
  zones,
  onSelectionChange,
  saving,
  activeIds,
  visibleCount,
}) => (
  <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 text-neutral-600">
    <span aria-live="polite">
      {onSelectionChange ? (
        <>
          Выбрано{' '}
          <strong className="text-neutral-800">{activeIds.length}</strong> из{' '}
          {zones.length}
        </>
      ) : (
        <>Участков: {zones.length}</>
      )}
    </span>
    {onSelectionChange && (
      <FormActions aria-label="Выбор рабочих участков" role="group">
        <Button
          variant="ghost"
          disabled={
            saving ||
            !zones.length ||
            zones.every((zone) => activeIds.includes(zone.id!))
          }
          onClick={() =>
            onSelectionChange(
              zones.flatMap((zone) => (zone.id ? [zone.id] : [])),
            )
          }
        >
          Выбрать все
        </Button>
        <Button
          variant="ghost"
          disabled={saving || !activeIds.length}
          onClick={() => onSelectionChange([])}
        >
          Снять выбор
        </Button>
      </FormActions>
    )}
    {visibleCount !== zones.length && <span>Найдено: {visibleCount}</span>}
  </div>
);
