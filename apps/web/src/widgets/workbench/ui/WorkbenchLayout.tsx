import { WorkbenchContribution } from '@/shared/layout/workbenchSlots';
import { featureAvailability } from '@/shared/config/featureAvailability';
import { cx } from '@green/ui';
import { useEffect, useId, useState, type CSSProperties, type FC } from 'react';
import { useIdeWorkspaceLayout } from '../model/useIdeWorkspaceLayout';
import { ResourcesDock } from './docks/ResourcesDock';
import { ResultsDock } from './docks/ResultsDock';
import { RightDock } from './docks/RightDock';
import type {
  IDEWorkspaceShellProps,
  IdeWorkspaceResultsTab,
  IdeWorkspaceRightTab,
} from './workbench.types';
import { resultsTabs, rightTabs } from './workbenchTabDefinitions';
export const WorkbenchLayout: FC<IDEWorkspaceShellProps> = (props) => {
  const {
    header,
    map,
    resources,
    inspector,
    tool,
    assistant,
    results,
    footer,
    className = '',
  } = props;
  const layout = useIdeWorkspaceLayout(props);
  const [internalRightTab, setInternalRightTab] =
    useState<IdeWorkspaceRightTab>(props.initialRightTab ?? 'inspector');
  const [internalResultsTab, setInternalResultsTab] =
    useState<IdeWorkspaceResultsTab>('checks');
  const requestedRightTab = props.rightTab ?? internalRightTab;
  const rightTab =
    requestedRightTab === 'assistant' && !featureAvailability.assistant
      ? 'tool'
      : requestedRightTab;
  const resultsTab = props.resultsTab ?? internalResultsTab;
  const uid = useId();
  const { prioritizeRight } = layout;
  useEffect(() => {
    prioritizeRight();
  }, [rightTab, prioritizeRight]);
  const rightContent = { inspector, tool, assistant };
  const chooseRight = (tab: IdeWorkspaceRightTab) => {
    if (props.rightTab === undefined) {
      setInternalRightTab(tab);
      layout.setRightOpen(true);
    }
    props.onRightTabChange?.(tab);
  };
  const chooseResults = (tab: IdeWorkspaceResultsTab) => {
    if (props.resultsTab === undefined) setInternalResultsTab(tab);
    props.onResultsTabChange?.(tab);
    layout.setResultsOpen(true);
  };
  const style = {
    '--ide-resources-width': `${layout.resourcesWidth}px`,
    '--ide-right-width': `${layout.rightWidth}px`,
    '--ide-results-height': `${layout.resultsTotalHeight}px`,
  } as CSSProperties;

  return (
    <div
      data-workbench
      className={cx(
        'flex h-dvh min-h-90 w-full flex-col overflow-hidden bg-white font-sans text-[13px] leading-normal text-neutral-800',
        className,
      )}
    >
      {props.children}
      <WorkbenchContribution slot="resources">
        {resources}
      </WorkbenchContribution>
      {rightTabs.map((tab) => (
        <WorkbenchContribution key={tab.id} slot={tab.id}>
          {rightContent[tab.id]}
        </WorkbenchContribution>
      ))}
      {resultsTabs.map((tab) => (
        <WorkbenchContribution key={tab.id} slot={tab.id}>
          {results[tab.id]}
        </WorkbenchContribution>
      ))}
      <div className="min-w-0 shrink-0">{header}</div>
      <div
        ref={layout.ref}
        className="grid min-h-0 min-w-0 flex-1 grid-cols-[var(--ide-resources-width)_minmax(0,1fr)_var(--ide-right-width)]"
        style={style}
        data-map-minimum={layout.mapMinimumSatisfied ? 'met' : 'limited'}
      >
        <ResourcesDock
          id={`${uid}-resources`}
          expanded={layout.resourcesExpanded}
          width={layout.resourcesWidth}
          min={layout.resourcesMin}
          max={layout.resourcesMax}
          onOpenChange={layout.setResourcesOpen}
          onResize={(value) => layout.setSize('resourcesWidth', value)}
          onReset={() => layout.resetSize('resourcesWidth')}
        />

        <section
          className="flex min-h-0 min-w-0 flex-col"
          aria-label="Рабочая область плана"
        >
          <div className="relative min-h-0 min-w-0 flex-1 overflow-hidden bg-neutral-100">
            {map}
          </div>
          <ResultsDock
            id={`${uid}-results`}
            open={layout.resultsOpen}
            height={layout.resultsHeight}
            min={layout.minResults}
            max={layout.maxResults}
            onOpenChange={layout.setResultsOpen}
            onResize={(value) => layout.setSize('resultsHeight', value)}
            onReset={() => layout.resetSize('resultsHeight')}
            resultsTab={resultsTab}
            chooseResults={chooseResults}
          />
        </section>

        <RightDock
          id={`${uid}-right`}
          expanded={layout.rightExpanded}
          width={layout.rightWidth}
          min={layout.rightMin}
          max={layout.rightMax}
          onOpenChange={layout.setRightOpen}
          onResize={(value) => layout.setSize('rightWidth', value)}
          onReset={() => layout.resetSize('rightWidth')}
          rightTab={rightTab}
          chooseRight={chooseRight}
        />
      </div>
      {footer !== undefined && (
        <div className="min-w-0 shrink-0 border-t border-neutral-300 bg-neutral-100">
          {footer}
        </div>
      )}
    </div>
  );
};
