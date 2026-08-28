import { MousePointer2, Shrub, Trash2, TreePine } from 'lucide-react';
import { Divider, IconButton, Toolbar } from '@green/ui';

export type MapTool = 'select' | 'add_tree' | 'add_shrub' | 'move' | 'draw_area';

export function MapToolbar({ tool, onTool, onDelete, canDelete, editable = true }: { tool: MapTool; onTool: (tool: MapTool) => void; onDelete: () => void; canDelete: boolean; editable?: boolean }) {
  return (
    <Toolbar className="map-toolbar">
      <IconButton icon={MousePointer2} label="Выбрать. Shift — добавить к выбору" active={tool === 'select'} aria-pressed={tool === 'select'} onClick={() => onTool('select')} />
      <Divider orientation="vertical" />
      <IconButton icon={TreePine} label="Добавить дерево" active={tool === 'add_tree'} aria-pressed={tool === 'add_tree'} disabled={!editable} onClick={() => onTool('add_tree')} />
      <IconButton icon={Shrub} label="Добавить кустарник" active={tool === 'add_shrub'} aria-pressed={tool === 'add_shrub'} disabled={!editable} onClick={() => onTool('add_shrub')} />
      {canDelete ? <><Divider orientation="vertical" /><IconButton icon={Trash2} label="Удалить выбранное" variant="danger" disabled={!editable} onClick={onDelete} /></> : null}
    </Toolbar>
  );
}
