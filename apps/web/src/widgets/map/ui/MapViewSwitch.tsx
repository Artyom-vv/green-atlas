import { SegmentedControl } from '@green/ui';
import type { FC } from 'react';

export type MapMode = '2d' | '3d';
export interface MapViewSwitchProps {
  mode: MapMode;
  onChange: (mode: MapMode) => void;
}

const modes = [
  { value: '2d', label: '2D' },
  { value: '3d', label: '3D' },
] as const;

export const MapViewSwitch: FC<MapViewSwitchProps> = ({ mode, onChange }) => (
  <SegmentedControl
    label="Режим отображения"
    controlSize="compact"
    value={mode}
    options={modes}
    onChange={onChange}
  />
);
