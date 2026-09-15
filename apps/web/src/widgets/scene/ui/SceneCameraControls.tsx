import type { FC } from 'react';
import { IconButton, Toolbar } from '@green/ui';
import { Focus, Minus, Plus, ScanSearch } from 'lucide-react';

const CAMERA_ZOOM_FACTORS = { in: 0.8, out: 1.25 } as const;

export interface SceneCameraControlsProps {
  hasSelection: boolean;
  onZoom: (factor: number) => void;
  onOverview: () => void;
  onFitSelection: () => void;
}

export const SceneCameraControls: FC<SceneCameraControlsProps> = ({
  hasSelection,
  onZoom,
  onOverview,
  onFitSelection,
}) => (
  <Toolbar
    orientation="vertical"
    label="Камера 3D"
    className="rounded-card absolute right-4 bottom-11 z-4 flex-col flex-nowrap shadow-sm"
  >
    <IconButton
      icon={Plus}
      label="Приблизить 3D"
      variant="ghost"
      onClick={() => onZoom(CAMERA_ZOOM_FACTORS.in)}
    />
    <IconButton
      icon={Minus}
      label="Отдалить 3D"
      variant="ghost"
      onClick={() => onZoom(CAMERA_ZOOM_FACTORS.out)}
    />
    <IconButton
      icon={ScanSearch}
      label="Показать весь чертёж в 3D"
      variant="ghost"
      onClick={onOverview}
    />
    <IconButton
      icon={Focus}
      label="Показать выбранные посадки"
      variant="ghost"
      disabled={!hasSelection}
      onClick={onFitSelection}
    />
  </Toolbar>
);
