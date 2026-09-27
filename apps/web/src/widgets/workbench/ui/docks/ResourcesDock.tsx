import { WorkbenchHost } from '@/shared/layout/workbenchSlots';
import { DockStrip } from '@/shared/layout/DockStrip';
import { IconButton, ResizeHandle } from '@green/ui';
import { FolderTree, PanelLeftClose } from 'lucide-react';
import { type FC } from 'react';
import { dock } from '../workbenchSurfaceVariants';

interface ResourcesDockProps {
  id: string;
  expanded: boolean;
  width: number;
  min: number;
  max: number;
  onOpenChange: (open: boolean) => void;
  onResize: (value: number) => void;
  onReset: () => void;
}
export const ResourcesDock: FC<ResourcesDockProps> = ({
  id,
  expanded,
  width,
  min,
  max,
  onOpenChange,
  onResize,
  onReset,
}) => (
  <aside className={dock({ side: 'resources' })} aria-label="Ресурсы проекта">
    <DockStrip
      edge={expanded ? 'bottom' : 'none'}
      actions={
        <IconButton
          icon={expanded ? PanelLeftClose : FolderTree}
          label={
            expanded ? 'Свернуть ресурсы проекта' : 'Показать ресурсы проекта'
          }
          variant="ghost"
          controlSize="compact"
          aria-expanded={expanded}
          aria-controls={id}
          onClick={() => onOpenChange(!expanded)}
        />
      }
    >
      <WorkbenchHost
        slot="resourcesNavigation"
        className="min-w-0"
        hidden={!expanded}
        inert={!expanded}
      />
    </DockStrip>
    <WorkbenchHost
      slot="resources"
      id={`${id}`}
      className="min-h-0 flex-1 overflow-hidden"
      hidden={!expanded}
      inert={!expanded}
    />
    <div hidden={!expanded}>
      <ResizeHandle
        className="absolute inset-y-0 -right-1 z-10 h-full w-2"
        label="Ширина ресурсов проекта"
        orientation="vertical"
        value={width}
        min={min}
        max={max}
        onChange={onResize}
        onReset={onReset}
      />
    </div>
  </aside>
);
