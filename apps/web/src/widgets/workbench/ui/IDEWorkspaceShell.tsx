import { WorkbenchSlotsProvider } from '@/shared/layout/workbenchSlots';
import { ControlProvider } from '@green/ui';
import type { FC } from 'react';
import type { IDEWorkspaceShellProps } from './workbench.types';
import { WorkbenchLayout } from './WorkbenchLayout';
export type {
  IdeRightTab,
  IdeWorkspaceResultsTab,
  IdeWorkspaceRightTab,
  IDEWorkspaceShellProps,
} from './workbench.types';
export const IDEWorkspaceShell: FC<IDEWorkspaceShellProps> = (props) => (
  <WorkbenchSlotsProvider>
    <ControlProvider size="compact">
      <WorkbenchLayout {...props} />
    </ControlProvider>
  </WorkbenchSlotsProvider>
);
