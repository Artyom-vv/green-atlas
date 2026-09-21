import { ProjectLayers, WorkspaceExplorer } from '@/widgets/project-explorer';
import { useEditorSession } from '@/entities/editor';
import type { FC } from 'react';
import type { WorkspaceResourcesProps } from './WorkspaceResources.props';
export { WorkspaceResourcesPropsFor } from './WorkspaceResources.props';

export const WorkspaceResources: FC<WorkspaceResourcesProps> = ({
  activeLayerId,
  changePreview,
  editorBusy,
  focusZones,
  hasUnsavedWork,
  layers,
  leaveWorkspace,
  navigateWorkspace,
  openRightPanel,
  planLocked,
  planObjects,
  project,
  projectId,
  selectFromExplorer,
  selectPatternZones,
  selectedIds,
  selectedPatternZoneIds,
  setActiveLayerId,
  setIdeRightTab,
  setLibraryOpen,
  setPanel,
  setVisibility,
  speciesNames,
  visibility,
  zoneDrawingMode,
}) => {
  const tab = useEditorSession((state) => state.resourcesTab);
  const setTab = useEditorSession((state) => state.setResourcesTab);
  return (
    <WorkspaceExplorer
      activeTab={tab}
      onTabChange={setTab}
      zones={project.planting_zones ?? []}
      objects={planObjects}
      selectedIds={selectedIds}
      selectedZoneIds={selectedPatternZoneIds}
      speciesNames={speciesNames}
      sourceName={project.source_file?.name ?? project.name}
      sourceCount={layers.reduce(
        (count, layer) => count + layer.object_count,
        0,
      )}
      zoneSelectionDisabled={
        Boolean(changePreview) ||
        editorBusy ||
        planLocked ||
        Boolean(zoneDrawingMode)
      }
      disabled={hasUnsavedWork || editorBusy}
      onZonesChange={selectPatternZones}
      onSelect={(ids) => selectFromExplorer(ids, false)}
      onZone={(zone) => focusZones([zone])}
      onManagePlantings={() => setLibraryOpen(true)}
      onManageZones={() => navigateWorkspace('zones')}
      onSource={() => leaveWorkspace(`/projects/${projectId}/setup`)}
      layers={
        <ProjectLayers
          layers={layers}
          visibility={visibility}
          activeLayerId={activeLayerId}
          onVisibility={(id, visible) =>
            setVisibility((current) => ({ ...current, [id]: visible }))
          }
          onGroupVisibility={(ids, visible) =>
            setVisibility((current) => ({
              ...current,
              ...Object.fromEntries(ids.map((id) => [id, visible])),
            }))
          }
          onSelect={(id) => {
            setActiveLayerId((current) => (current === id ? undefined : id));
            setPanel(null);
            openRightPanel();
            setIdeRightTab('inspector');
          }}
        />
      }
    />
  );
};
