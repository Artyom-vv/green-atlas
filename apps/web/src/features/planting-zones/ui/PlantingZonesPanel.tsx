import type { FC } from 'react';
import type { PlantingZoneAssignment } from '@green/api-client';
import { Edit3, Trash2 } from 'lucide-react';
import { Button, FormActions, IconButton, InlineMessage } from '@green/ui';
import { repeatedItemLabel } from '@/entities/planting-zone/model/plantingZoneLabels';
import { EditorPanel } from '@/shared/ui/inspector';

export interface PlantingZonesPanelProps {
  assignments: PlantingZoneAssignment[];
  drawingManual?: boolean;
  saving?: boolean;
  error?: string;
  onRemove: (assignmentId: string) => void;
  onSave: () => void;
  onManual: () => void;
  onCancelManual: () => void;
  onClose?: () => void;
}
export const PlantingZonesPanel: FC<PlantingZonesPanelProps> = ({
  assignments,
  drawingManual = false,
  saving = false,
  error,
  onRemove,
  onSave,
  onManual,
  onCancelManual,
  onClose,
}) => (
  <EditorPanel title="Выберите место" onClose={onClose}>
    {assignments.length ? (
      <>
        <h3 className="m-0 text-sm font-semibold">
          Выбрано {assignments.length}
        </h3>
        <div className="grid gap-1">
          {assignments.map((assignment) => {
            const label = repeatedItemLabel(assignments, assignment);
            return (
              <div
                key={assignment.id ?? assignment.label}
                className="flex min-w-0 items-center justify-between gap-2 border-0 border-b border-solid border-neutral-200 py-2"
              >
                <strong className="min-w-0 text-xs font-medium wrap-anywhere">
                  {label}
                </strong>
                <IconButton
                  icon={Trash2}
                  label={`Убрать ${label}`}
                  variant="ghost"
                  onClick={() => onRemove(assignment.id ?? '')}
                />
              </div>
            );
          })}
        </div>
      </>
    ) : (
      <p className="m-0 text-xs leading-4 text-neutral-600">
        Кликните контур на карте или нарисуйте участок.
      </p>
    )}
    {drawingManual ? (
      <>
        <p className="m-0 text-xs leading-4 text-neutral-600">
          Поставьте точки и замкните контур
        </p>
        <FormActions layout="equal" minItemWidth="100%">
          <Button variant="secondary" onClick={onCancelManual}>
            Отмена
          </Button>
        </FormActions>
      </>
    ) : (
      <FormActions layout="equal" minItemWidth="100%">
        <Button variant="secondary" icon={Edit3} onClick={onManual}>
          Нарисовать область
        </Button>
      </FormActions>
    )}
    {error && <InlineMessage tone="error">{error}</InlineMessage>}
    {!!assignments.length && (
      <FormActions layout="equal" minItemWidth="100%">
        <Button
          variant="primary"
          loading={saving}
          disabled={saving}
          onClick={onSave}
        >
          Открыть редактор
        </Button>
      </FormActions>
    )}
  </EditorPanel>
);
