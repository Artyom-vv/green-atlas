import type { WorkspaceSelectionInspectorProps } from './WorkspaceSelectionInspector.props';
import type { FC } from 'react';
import {
  ObjectInspector,
  type ObjectInspectorProps,
} from '@/widgets/selection-inspector/ui/ObjectInspector';
import { GroupInspector } from '@/widgets/selection-inspector/ui/GroupInspector';
export const WorkspaceSelectionInspector: FC<
  WorkspaceSelectionInspectorProps
> = ({
  inspectorView,
  selectedObject,
  planLocked,
  editorBusy,
  previewSelectionLock,
  sceneOpen,
  sceneReview,
  mapViewport,
  selectedIds,
  changeMapMode,
  activateTool,
  speciesNames,
  growthHorizon,
  setGrowthHorizon,
  setSpeciesAssignmentOpen,
  setDeleteSelectionOpen,
  selectedObjects,
  issues,
}) => {
  const mapMode: ObjectInspectorProps['mapMode'] = sceneOpen ? '3d' : '2d';
  const beginTransform = (tool: 'move' | 'copy') => {
    if (sceneOpen) changeMapMode('2d');
    activateTool(tool);
  };
  const common = {
    mapMode,
    growthHorizon,
    onGrowthHorizon: setGrowthHorizon,
    onFit: () =>
      sceneOpen
        ? sceneReview.current?.fitSelection()
        : mapViewport.current?.fitObjects(selectedIds),
    onMove: () => beginTransform('move'),
    onSpecies: () => setSpeciesAssignmentOpen(true),
    onDelete: () => setDeleteSelectionOpen(true),
  };
  return (
    <>
      {inspectorView === 'object' && selectedObject && (
        <ObjectInspector
          {...common}
          onLock={!planLocked && !editorBusy ? previewSelectionLock : undefined}

          object={selectedObject}
          speciesName={
            selectedObject.species_revision_id
              ? speciesNames.get(selectedObject.species_revision_id)
              : undefined
          }

          editable={!planLocked && !editorBusy && !selectedObject.locked}
        />
      )}
      {inspectorView === 'group' && (
        <GroupInspector
          {...common}

          objects={selectedObjects}
          issues={issues}
          disabled={editorBusy || planLocked}

          onCopy={() => beginTransform('copy')}
          onLock={previewSelectionLock}
        />
      )}
    </>
  );
};
