import type { IdeWorkspaceRightTab } from '../workbench.types';
export interface RightDockProps {
  id: string;
  expanded: boolean;
  width: number;
  min: number;
  max: number;
  onOpenChange: (open: boolean) => void;
  onResize: (value: number) => void;
  onReset: () => void;
  rightTab: IdeWorkspaceRightTab;
  chooseRight: (tab: IdeWorkspaceRightTab) => void;
}
