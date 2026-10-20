import { WorkbenchHost } from '@/shared/layout/workbenchSlots';
import { DockStrip } from '@/shared/layout/DockStrip';
import { RESULTS_STRIP_HEIGHT } from '@/shared/ui/results/resultsLayout';
import { ControlProvider, IconButton, ResizeHandle } from '@green/ui';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { type CSSProperties, type FC, type ReactNode } from 'react';
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
  actions?: ReactNode;
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
  actions,
}) => (
  <ControlProvider size="compact">
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
        edge="none"
        style={
          {
            '--dock-strip-height': `${RESULTS_STRIP_HEIGHT}px`,
          } as CSSProperties
        }
        className={`relative p-2 before:pointer-events-none before:absolute before:inset-x-0 before:top-0 before:h-px before:bg-neutral-300 ${open ? 'after:pointer-events-none after:absolute after:inset-x-0 after:bottom-0 after:h-px after:bg-neutral-200' : ''}`}
        actions={
          <>
            {actions}
            <IconButton
              icon={open ? ChevronDown : ChevronUp}
              label={open ? 'Свернуть результаты' : 'Развернуть результаты'}
              variant="ghost"
              controlSize="compact"
              aria-expanded={open}
              aria-controls={`${id}-${resultsTab}`}
              onClick={() => onOpenChange(!open)}
            />
          </>
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
  </ControlProvider>
);
