import type { EditorRightTab } from '@/entities/editor/model/editorTypes';
import type { ReactNode } from 'react';
import type { IdeWorkspaceLayoutOptions } from '../model/useIdeWorkspaceLayout';
export type IdeWorkspaceRightTab = EditorRightTab;
export type IdeRightTab = IdeWorkspaceRightTab;
export type IdeWorkspaceResultsTab = 'checks' | 'schedule' | 'history';
export interface IDEWorkspaceShellProps extends IdeWorkspaceLayoutOptions {
  children?: ReactNode;
  header: ReactNode;
  map: ReactNode;
  resources: ReactNode;
  inspector: ReactNode;
  tool: ReactNode;
  assistant: ReactNode;
  results: Record<IdeWorkspaceResultsTab, ReactNode>;
  footer?: ReactNode;
  rightTab?: IdeWorkspaceRightTab;
  onRightTabChange?: (tab: IdeWorkspaceRightTab) => void;
  initialRightTab?: IdeWorkspaceRightTab;
  resultsTab?: IdeWorkspaceResultsTab;
  onResultsTabChange?: (tab: IdeWorkspaceResultsTab) => void;
  className?: string;
}
