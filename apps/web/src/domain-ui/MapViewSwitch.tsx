import { Button } from '@green/ui';
import { MapControlGroup } from './MapControlGroup';

export function MapViewSwitch({ mode, onChange }: { mode: '2d' | '3d'; onChange: (mode: '2d' | '3d') => void }) {
  return <MapControlGroup className="editor-map-mode" label="Режим отображения">
    <Button variant={mode === '2d' ? 'primary' : 'ghost'} controlSize="compact" aria-pressed={mode === '2d'} onClick={() => onChange('2d')}>2D</Button>
    <Button variant={mode === '3d' ? 'primary' : 'ghost'} controlSize="compact" aria-pressed={mode === '3d'} onClick={() => onChange('3d')}>3D</Button>
  </MapControlGroup>;
}
