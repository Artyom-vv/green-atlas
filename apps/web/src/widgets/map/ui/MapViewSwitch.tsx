import { SegmentedControl } from '@green/ui';
import type { FC } from 'react';
import { CanvasModeSwitch } from '@/shared/ui/CanvasControls';

export type MapMode = '2d' | '3d';
export interface MapViewSwitchProps {
  mode: MapMode;
  onChange: (mode: MapMode) => void;
  embedded?: boolean;
  disabled3d?: boolean;
}

const modes = [
  { value: '2d', label: '2D' },
  { value: '3d', label: '3D' },
] as const;

export const MapViewSwitch: FC<MapViewSwitchProps> = ({
  mode,
  onChange,
  embedded = false,
  disabled3d = false,
}) =>
  embedded ? (
    <CanvasModeSwitch
      label="Режим отображения"
      value={mode}
      onChange={onChange}
      options={modes.map((option) => ({
        ...option,
        disabled: option.value === '3d' && disabled3d,
        title:
          option.value === '3d' && disabled3d
            ? 'Завершите сравнение, чтобы открыть 3D'
            : undefined,
      }))}
    />
  ) : (
    <SegmentedControl
      label="Режим отображения"
      controlSize="compact"
      className={embedded ? 'gap-1 border-0 bg-transparent p-0' : undefined}
      optionClassName={
        embedded
          ? 'w-(--control-height) flex-none px-0 aria-pressed:border-blue-600 aria-pressed:bg-blue-600 aria-pressed:text-white'
          : undefined
      }
      value={mode}
      options={modes}
      onChange={onChange}
    />
  );
