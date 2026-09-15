import { WorkbenchHost } from '@/shared/layout/workbenchSlots';
import { DockStrip } from '@/shared/layout/DockStrip';
import { IconButton, ResizeHandle } from '@green/ui';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { type FC } from 'react';
import type { IdeWorkspaceResultsTab } from '../workbench.types';
import { dock } from '../workbenchSurfaceVariants';
import { resultsTabs } from '../workbenchTabDefinitions';
import { WorkbenchTabs } from '../WorkbenchTabs';
interface ResultsDockProps {
  id: string;
  open: boolean;
  height: number;
  min: number;
  max: number;
  onOpenChange: (open: boolean) => void;
  onResize: (value: number) => void;
  onReset: () => void;
  resultsTab: IdeWorkspaceResultsTab;
  chooseResults: (tab: IdeWorkspaceResultsTab) => void;
}
export const ResultsDock: FC<ResultsDockProps> = ({
  id,
  open,
  height,
  min,
  max,
  onOpenChange,
  resultsTab,
  chooseResults,
  onResize,
  onReset,
}) => (
  <section
    className={dock({ side: 'results' })}
    aria-label="Результаты проекта"
  >
    <div hidden={!open}>
      <ResizeHandle
        className="absolute inset-x-0 -top-1 z-10 h-2 w-full"
        label="Высота результатов"
        orientation="horizontal"
        reverse
        value={height}
        min={min}
        max={max}
        onChange={onResize}
        onReset={onReset}
      />
    </div>
    <DockStrip
      edge="top"
      actions={
        <IconButton
          icon={open ? ChevronDown : ChevronUp}
          label={open ? 'Свернуть результаты' : 'Развернуть результаты'}
          variant="ghost"
          controlSize="compact"
          aria-expanded={open}
          onClick={() => onOpenChange(!open)}
        />
      }
    >
      <WorkbenchTabs
        id={id}
        label="Результаты"
        tabs={resultsTabs}
        value={resultsTab}
        onChange={chooseResults}
      />
    </DockStrip>
    {resultsTabs.map((tab) => (
      <WorkbenchHost
        slot={tab.id}
        key={tab.id}
        id={`${id}-${tab.id}`}
        role="tabpanel"
        aria-labelledby={`${id}-tab-${tab.id}`}
        className="min-h-0 min-w-0 flex-1 overflow-hidden"
        hidden={!open || resultsTab !== tab.id}
        inert={!open || resultsTab !== tab.id}
      />
    ))}
  </section>
);
