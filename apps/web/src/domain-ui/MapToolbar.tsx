import { BoxSelect, Grid3x3, Hand, LassoSelect, MousePointer2, Paintbrush, Route, Shrub, TreeDeciduous, Trash2 } from 'lucide-react';
import { Divider, IconButton } from '@green/ui';
import { MapControlGroup } from './MapControlGroup';

export type MapTool = 'select' | 'pan' | 'select_box' | 'select_lasso' | 'add_tree' | 'add_shrub' | 'pattern_row' | 'pattern_fill' | 'brush' | 'move' | 'copy' | 'draw_area';

export function MapToolbar({ tool, onTool, onDelete, canDelete, editable = true, mapMode = '2d' }: { tool: MapTool; onTool: (tool: MapTool) => void; onDelete: () => void; canDelete: boolean; editable?: boolean; mapMode?: '2d' | '3d' }) {
  return (
    <MapControlGroup className="editor-toolbar" orientation="vertical" label="Инструменты карты">
      <IconButton icon={MousePointer2} label="Выбрать. Shift — добавить к выбору" active={tool === 'select'} aria-pressed={tool === 'select'} onClick={() => onTool('select')} />
      <IconButton icon={BoxSelect} label={mapMode === '3d' ? 'Выбрать рамкой в 2D' : 'Выбрать рамкой'} active={tool === 'select_box'} aria-pressed={tool === 'select_box'} onClick={() => onTool('select_box')} />
      <IconButton icon={LassoSelect} label={mapMode === '3d' ? 'Выбрать лассо в 2D' : 'Выбрать лассо'} active={tool === 'select_lasso'} aria-pressed={tool === 'select_lasso'} onClick={() => onTool('select_lasso')} />
      <IconButton icon={Hand} label="Перемещать карту" active={tool === 'pan'} aria-pressed={tool === 'pan'} onClick={() => onTool('pan')} />
      <Divider orientation="vertical" />
      <IconButton icon={Grid3x3} label={mapMode === '3d' ? 'Разместить посадки в 2D' : 'Разместить посадки'} active={tool === 'pattern_fill'} aria-pressed={tool === 'pattern_fill'} disabled={!editable} onClick={() => onTool('pattern_fill')} />
      <IconButton icon={Paintbrush} label={mapMode === '3d' ? 'Кисть посадок в 2D' : 'Кисть посадок'} active={tool === 'brush'} aria-pressed={tool === 'brush'} disabled={!editable} onClick={() => onTool('brush')} />
      <IconButton icon={Route} label={mapMode === '3d' ? 'Посадки вдоль линии в 2D' : 'Посадки вдоль линии'} active={tool === 'pattern_row'} aria-pressed={tool === 'pattern_row'} disabled={!editable} onClick={() => onTool('pattern_row')} />
      <Divider orientation="vertical" />
      <IconButton icon={TreeDeciduous} label={mapMode === '3d' ? 'Посадить дерево в 2D' : 'Посадить дерево'} active={tool === 'add_tree'} aria-pressed={tool === 'add_tree'} disabled={!editable} onClick={() => onTool('add_tree')} />
      <IconButton icon={Shrub} label={mapMode === '3d' ? 'Посадить кустарник в 2D' : 'Посадить кустарник'} active={tool === 'add_shrub'} aria-pressed={tool === 'add_shrub'} disabled={!editable} onClick={() => onTool('add_shrub')} />
      {canDelete ? <><Divider orientation="vertical" /><IconButton icon={Trash2} label="Удалить выбранное" variant="danger" disabled={!editable} onClick={onDelete} /></> : null}
    </MapControlGroup>
  );
}
