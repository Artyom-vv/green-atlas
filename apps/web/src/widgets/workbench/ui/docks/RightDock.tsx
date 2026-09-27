import { WorkbenchHost } from '@/shared/layout/workbenchSlots';
import { ResizeHandle } from '@green/ui';
import { type FC } from 'react';
import { dock, rightPanel } from '../workbenchSurfaceVariants';
import { rightTabs } from '../workbenchTabDefinitions';
import type { RightDockProps } from './rightDock.types';
import { RightDockNavigation } from './RightDockNavigation';

export const RightDock: FC<RightDockProps> = ({
  id,
  expanded,
  width,
  min,
  max,
  onOpenChange,
  rightTab,
  chooseRight,
  onResize,
  onReset,
}) => (
  <aside
    className={dock({ side: 'right' })}
    aria-label="Свойства и текущая задача"
  >
    <RightDockNavigation
      id={id}
      expanded={expanded}
      rightTab={rightTab}
      chooseRight={chooseRight}
      onOpenChange={onOpenChange}
    />
    <div
      className="flex min-h-0 flex-1 flex-col"
      hidden={!expanded}
      inert={!expanded}
    >
      <div className="relative min-h-0 min-w-0 flex-1 overflow-hidden">
        {rightTabs.map((tab) => (
          <WorkbenchHost
            slot={tab.id}
            key={tab.id}
            id={`${id}-${tab.id}`}
            role="tabpanel"
            aria-labelledby={`${id}-tab-${tab.id}`}
            className={rightPanel()}
            hidden={rightTab !== tab.id}
            inert={rightTab !== tab.id}
          />
        ))}
      </div>
    </div>
    <div hidden={!expanded}>
      <ResizeHandle
        className="absolute inset-y-0 -left-1 z-10 h-full w-2"
        label="Ширина правой области"
        orientation="vertical"
        reverse
        value={width}
        min={min}
        max={max}
        onChange={onResize}
        onReset={onReset}
      />
    </div>
  </aside>
);
