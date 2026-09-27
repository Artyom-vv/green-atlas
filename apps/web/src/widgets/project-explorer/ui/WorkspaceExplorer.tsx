import type { WorkspaceExplorerProps } from './WorkspaceExplorer.props';
export type { WorkspaceExplorerProps } from './WorkspaceExplorer.props';
import { Button, ControlProvider, ScrollArea } from '@green/ui';
import { DockStrip } from '@/shared/layout/DockStrip';
import { WorkbenchContribution } from '@/shared/layout/workbenchSlots';
import { Settings2 } from 'lucide-react';
import { useState, type FC } from 'react';
import { ExplorerPlantings } from './ExplorerPlantings';
import { ExplorerZones } from './ExplorerZones';
import { ExplorerNavigation, type ExplorerTab } from './ExplorerNavigation';

export const WorkspaceExplorer: FC<WorkspaceExplorerProps> = ({
  activeTab,
  onTabChange,
  zones,
  objects,
  selectedIds,
  selectedZoneIds,
  speciesNames,
  layers,
  sourceName,
  sourceCount,
  onSelect,
  onZone,
  onZonesChange,
  onManageZones,
  onManagePlantings,
  onSource,
  disabled = false,
  zoneSelectionDisabled = false,
}) => {
  const [localTab, setLocalTab] = useState<ExplorerTab>('project');
  const tab = activeTab ?? localTab;
  const navigation = (
    <ExplorerNavigation
      tab={tab}
      onChange={(next) => {
        setLocalTab(next);
        onTabChange?.(next);
      }}
    />
  );
  return (
    <ControlProvider size="compact">
      <div className="flex h-full min-h-0 min-w-0 flex-col bg-white">
        <WorkbenchContribution
          slot="resourcesNavigation"
          fallback={<DockStrip>{navigation}</DockStrip>}
        >
          {navigation}
        </WorkbenchContribution>
        <ScrollArea
          className="flex-1"
          contentClassName="p-2"
          hidden={tab !== 'project'}
        >
          <ExplorerZones
            zones={zones}
            selectedIds={selectedZoneIds}
            selectionDisabled={zoneSelectionDisabled}
            managementDisabled={disabled}
            onSelectionChange={onZonesChange}
            onFocus={onZone}
            onManage={onManageZones}
          />
          <ExplorerPlantings
            objects={objects}
            speciesNames={speciesNames}
            selectedIds={selectedIds}
            disabled={disabled}
            onSelect={onSelect}
            onManage={onManagePlantings}
          />
        </ScrollArea>
        <div
          className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden"
          hidden={tab !== 'layers'}
        >
          {layers}
        </div>
        <DockStrip edge="top" aria-label="Источники проекта">
          <Button
            variant="ghost"
            className="w-full justify-start"
            icon={<Settings2 />}
            title={`${sourceName ?? 'Исходный DXF'}: ${sourceCount.toLocaleString('ru')} объектов`}
            onClick={onSource}
          >
            Исходные данные
          </Button>
        </DockStrip>
      </div>
    </ControlProvider>
  );
};
