import { BoxSelect, Grid3x3, MousePointer2, Paintbrush, Route, Trash2 } from 'lucide-react';
import { Divider, IconButton } from '@green/ui';
import { MapControlGroup } from './MapControlGroup';

export type MapTool = 'select' | 'select_box' | 'select_lasso' | 'add_tree' | 'add_shrub' | 'pattern_row' | 'pattern_fill' | 'brush' | 'move' | 'copy' | 'draw_area';

export function MapToolbar({ tool, onTool, onDelete, canDelete, editable = true }: { tool: MapTool; onTool: (tool: MapTool) => void; onDelete: () => void; canDelete: boolean; editable?: boolean }) {
  return (
    <MapControlGroup className="map-toolbar" label="Инструменты карты">
      <IconButton icon={MousePointer2} label="Выбрать. Shift — добавить к выбору" active={tool === 'select'} aria-pressed={tool === 'select'} onClick={() => onTool('select')} />
      <IconButton icon={BoxSelect} label="Выбрать рамкой" active={tool === 'select_box'} aria-pressed={tool === 'select_box'} onClick={() => onTool('select_box')} />
      <Divider orientation="vertical" />
      <IconButton icon={Grid3x3} label="Разместить посадки" active={tool === 'pattern_fill'} aria-pressed={tool === 'pattern_fill'} disabled={!editable} onClick={() => onTool('pattern_fill')} />
      <IconButton icon={Paintbrush} label="Кисть посадок" active={tool === 'brush'} aria-pressed={tool === 'brush'} disabled={!editable} onClick={() => onTool('brush')} />
      <IconButton icon={Route} label="Посадки вдоль линии" active={tool === 'pattern_row'} aria-pressed={tool === 'pattern_row'} disabled={!editable} onClick={() => onTool('pattern_row')} />
      {canDelete ? <><Divider orientation="vertical" /><IconButton icon={Trash2} label="Удалить выбранное" variant="danger" disabled={!editable} onClick={onDelete} /></> : null}
    </MapControlGroup>
  );
}
