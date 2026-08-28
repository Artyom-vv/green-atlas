import { BoxSelect, Grid3x3, LassoSelect, MousePointer2, Rows3, Shrub, Trash2, TreePine } from 'lucide-react';
import { Divider, IconButton, Toolbar } from '@green/ui';

export type MapTool = 'select' | 'select_box' | 'select_lasso' | 'add_tree' | 'add_shrub' | 'pattern_row' | 'pattern_fill' | 'move' | 'copy' | 'draw_area';

export function MapToolbar({ tool, onTool, onDelete, canDelete, editable = true }: { tool: MapTool; onTool: (tool: MapTool) => void; onDelete: () => void; canDelete: boolean; editable?: boolean }) {
  return (
    <Toolbar className="map-toolbar">
      <IconButton icon={MousePointer2} label="Выбрать. Shift — добавить к выбору" active={tool === 'select'} aria-pressed={tool === 'select'} onClick={() => onTool('select')} />
      <IconButton icon={BoxSelect} label="Выбрать рамкой" active={tool === 'select_box'} aria-pressed={tool === 'select_box'} onClick={() => onTool('select_box')} />
      <IconButton icon={LassoSelect} label="Выбрать произвольным контуром" active={tool === 'select_lasso'} aria-pressed={tool === 'select_lasso'} onClick={() => onTool('select_lasso')} />
      <Divider orientation="vertical" />
      <IconButton icon={TreePine} label="Добавить дерево" active={tool === 'add_tree'} aria-pressed={tool === 'add_tree'} disabled={!editable} onClick={() => onTool('add_tree')} />
      <IconButton icon={Shrub} label="Добавить кустарник" active={tool === 'add_shrub'} aria-pressed={tool === 'add_shrub'} disabled={!editable} onClick={() => onTool('add_shrub')} />
      <IconButton icon={Rows3} label="Создать ряд" active={tool === 'pattern_row'} aria-pressed={tool === 'pattern_row'} disabled={!editable} onClick={() => onTool('pattern_row')} />
      <IconButton icon={Grid3x3} label="Заполнить участки" active={tool === 'pattern_fill'} aria-pressed={tool === 'pattern_fill'} disabled={!editable} onClick={() => onTool('pattern_fill')} />
      {canDelete ? <><Divider orientation="vertical" /><IconButton icon={Trash2} label="Удалить выбранное" variant="danger" disabled={!editable} onClick={onDelete} /></> : null}
    </Toolbar>
  );
}
