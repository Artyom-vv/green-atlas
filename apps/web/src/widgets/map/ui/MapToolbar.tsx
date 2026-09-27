import type { MapTool } from '@/entities/editor';
import { MapControlGroup } from '@/widgets/map/ui/MapControlGroup';
import { Divider, IconButton } from '@green/ui';
import { Trash2 } from 'lucide-react';
import { Fragment, type FC } from 'react';
import { toolbarGroups, type ToolbarItem } from './toolbarItems';

export type { MapTool } from '@/entities/editor';

export interface MapToolbarProps {
  tool: MapTool;
  onTool: (tool: MapTool) => void;
  onDelete: () => void;
  canDelete: boolean;
  editable?: boolean;
  mapMode?: '2d' | '3d';
}

export const MapToolbar: FC<MapToolbarProps> = ({
  tool,
  onTool,
  onDelete,
  canDelete,
  editable = true,
  mapMode = '2d',
}) => (
  <MapControlGroup
    className="max-h-[calc(100dvh-150px)] overflow-y-auto max-[600px]:max-h-90"
    orientation="vertical"
    label="Инструменты карты"
  >
    {toolbarGroups.map((group, index) => (
      <Fragment key={group[0].tool}>
        {index > 0 && <Divider />}
        {group.map((item: ToolbarItem) => (
          <IconButton
            key={item.tool}
            variant="ghost"
            icon={item.icon}
            label={
              item.requires2D && mapMode === '3d'
                ? `${item.label} в 2D`
                : item.label
            }
            active={tool === item.tool}
            aria-pressed={tool === item.tool}
            disabled={Boolean(item.editsPlan) && !editable}
            onClick={() => onTool(item.tool)}
          />
        ))}
      </Fragment>
    ))}
    {canDelete && (
      <>
        <Divider />
        <IconButton
          icon={Trash2}
          label="Удалить выбранное"
          variant="danger"
          disabled={!editable}
          onClick={onDelete}
        />
      </>
    )}
  </MapControlGroup>
);
