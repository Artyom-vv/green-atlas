import type { FC } from 'react';
import {
  CanvasControlGroup,
  CanvasControlSlot,
} from '@/shared/ui/CanvasControls';
import { Divider, IconButton } from '@green/ui';
import {
  Focus,
  LandPlot,
  Minus,
  Plus,
  ScanSearch,
  PersonStanding,
  AlignHorizontalJustifyCenter,
} from 'lucide-react';

const CAMERA_ZOOM_FACTORS = { in: 0.8, out: 1.25 } as const;

export interface SceneCameraControlsProps {
  disabled?: boolean;
  streetDisabled?: boolean;
  hasSelection: boolean;
  onZoom: (factor: number) => void;
  onOverview: () => void;
  onFitSelection: () => void;
  onFitZones?: () => void;
  zoneCount?: number;
  onStreet?: () => void;
  streetPicking?: boolean;
  streetView?: boolean;
  onLevelHorizon?: () => void;
}

export const SceneCameraControls: FC<SceneCameraControlsProps> = ({
  disabled = false,
  streetDisabled = false,
  hasSelection,
  onZoom,
  onOverview,
  onFitSelection,
  onFitZones,
  zoneCount = 0,
  onStreet,
  streetPicking,
  streetView,
  onLevelHorizon,
}) => (
  <CanvasControlSlot position="bottomRight">
    <CanvasControlGroup orientation="vertical" label="Камера 3D">
      <IconButton
        icon={Plus}
        label="Приблизить 3D"
        disabled={disabled}
        variant="ghost"
        onClick={() => onZoom(CAMERA_ZOOM_FACTORS.in)}
      />
      <IconButton
        icon={Minus}
        label="Отдалить 3D"
        disabled={disabled}
        variant="ghost"
        onClick={() => onZoom(CAMERA_ZOOM_FACTORS.out)}
      />
      <Divider />
      <IconButton
        icon={ScanSearch}
        label="Показать весь чертёж в 3D"
        disabled={disabled}
        variant="ghost"
        onClick={onOverview}
      />
      {onFitZones && zoneCount > 0 && (
        <IconButton
          icon={LandPlot}
          label={`Показать выбранные участки (${zoneCount})`}
          variant="ghost"
          disabled={disabled}
          onClick={onFitZones}
        />
      )}
      <IconButton
        icon={Focus}
        label="Показать выбранные посадки"
        variant="ghost"
        disabled={disabled || !hasSelection}
        onClick={onFitSelection}
      />
      {onStreet && (
        <>
          <Divider />
          <IconButton
            icon={PersonStanding}
            label={
              streetPicking
                ? 'Отменить выбор уличного ракурса'
                : 'Выбрать уличный ракурс'
            }
            variant="ghost"
            active={streetPicking}
            disabled={disabled || streetDisabled}
            aria-pressed={Boolean(streetPicking)}
            onClick={onStreet}
          />
        </>
      )}
      {onLevelHorizon && streetView && !streetPicking && (
        <IconButton
          icon={AlignHorizontalJustifyCenter}
          label="Выровнять горизонт"
          variant="ghost"
          disabled={disabled}
          onClick={onLevelHorizon}
        />
      )}
    </CanvasControlGroup>
  </CanvasControlSlot>
);
