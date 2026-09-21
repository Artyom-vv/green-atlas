import type { FC } from 'react';
import type { PlantingZoneAssignment } from '@green/api-client';
import { Plus } from 'lucide-react';
import { Button, Checkbox, Disclosure, FormActions } from '@green/ui';
import { repeatedItemLabel } from '../model/plantingZoneLabels';

export interface PlantingZonePickerProps {
  zones: PlantingZoneAssignment[];
  selectedIds: string[];
  expanded?: boolean;
  disabled?: boolean;
  drawing?: boolean;
  onChange: (ids: string[]) => void;
  onCreate?: () => void;
  onCancelCreate?: () => void;
}

export const PlantingZonePicker: FC<PlantingZonePickerProps> = ({
  zones,
  selectedIds,
  expanded = false,
  disabled = false,
  drawing = false,
  onChange,
  onCreate,
  onCancelCreate,
}) => {
  const selected = new Set(selectedIds);
  const available = zones.filter(
    (zone): zone is PlantingZoneAssignment & { id: string } => Boolean(zone.id),
  );
  const chosen = available.filter((zone) => selected.has(zone.id));
  const title =
    chosen.length === 1
      ? repeatedItemLabel(available, chosen[0])
      : chosen.length
        ? `Выбрано участков: ${chosen.length}`
        : 'Выберите участки';
  const choices = (
    <div className="grid min-w-0 gap-3">
      {available.length > 1 ? (
        <FormActions layout="equal" minItemWidth="100%">
          <Button
            variant="secondary"
            controlSize="compact"
            disabled={disabled || drawing}
            onClick={() =>
              onChange(
                chosen.length === available.length
                  ? []
                  : available.map((zone) => zone.id),
              )
            }
          >
            {chosen.length === available.length ? 'Снять выбор' : 'Выбрать все'}
          </Button>
        </FormActions>
      ) : null}
      <div className="grid min-w-0">
        {available.map((zone) => (
          <Checkbox
            key={zone.id}
            controlSize="compact"
            label={repeatedItemLabel(available, zone)}
            checked={selected.has(zone.id)}
            disabled={disabled || drawing}
            onChange={(event) =>
              onChange(
                event.target.checked
                  ? [...new Set([...selectedIds, zone.id])]
                  : selectedIds.filter((id) => id !== zone.id),
              )
            }
          />
        ))}
      </div>
      {onCreate ? (
        <FormActions layout="equal" minItemWidth="100%">
          <Button
            variant="secondary"
            controlSize="compact"
            icon={drawing ? undefined : Plus}
            disabled={disabled}
            onClick={drawing ? onCancelCreate : onCreate}
          >
            {drawing ? 'Отмена' : 'Новый участок'}
          </Button>
        </FormActions>
      ) : null}
    </div>
  );
  return (
    <section className="grid min-w-0 gap-3" aria-label="Участки размещения">
      {expanded ? (
        <>
          <h3 className="m-0 text-sm font-semibold">Где разместить посадки?</h3>
          {choices}
        </>
      ) : (
        <Disclosure title={title} defaultOpen={!chosen.length}>
          {choices}
        </Disclosure>
      )}
    </section>
  );
};
