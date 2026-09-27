import { IconButton } from '@green/ui';
import { DockStrip } from '@/shared/layout/DockStrip';
import { PanelRightClose } from 'lucide-react';
import { type FC } from 'react';
import { rightTabs } from '../workbenchTabDefinitions';
import { WorkbenchTabs } from '../WorkbenchTabs';
import type { RightDockProps } from './rightDock.types';
interface RightDockNavigationProps extends Pick<
  RightDockProps,
  'id' | 'expanded' | 'rightTab' | 'chooseRight' | 'onOpenChange'
> {}
export const RightDockNavigation: FC<RightDockNavigationProps> = ({
  id,
  expanded,
  rightTab,
  chooseRight,
  onOpenChange,
}) => (
  <>
    <DockStrip
      actions={
        <IconButton
          icon={PanelRightClose}
          label={
            expanded ? 'Свернуть правую область' : 'Показать правую область'
          }
          variant="ghost"
          controlSize="compact"
          aria-expanded={expanded}
          onClick={() => onOpenChange(!expanded)}
        />
      }
    >
      <WorkbenchTabs
        id={id}
        label="Правая область"
        tabs={rightTabs}
        value={rightTab}
        onChange={chooseRight}
        hidden={!expanded}
      />
    </DockStrip>
    <div className="flex flex-col items-center gap-1 py-1" hidden={expanded}>
      {rightTabs.map((tab) => (
        <IconButton
          key={tab.id}
          icon={tab.icon}
          label={tab.label}
          variant="ghost"
          controlSize="compact"
          active={rightTab === tab.id}
          onClick={() => chooseRight(tab.id)}
        />
      ))}
    </div>
  </>
);
