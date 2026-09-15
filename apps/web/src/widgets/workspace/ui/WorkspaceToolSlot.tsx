import type { FC, ReactNode } from 'react';
import { SinglePlacementPanel } from '@/features/placement';
import { errorMessage } from '@/shared/errors/errorMessage';
import { EditorPanel } from '@/shared/ui/inspector/EditorPanel';
import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';

export interface WorkspaceToolSlotProps extends Pick<
  WorkspaceReadyModel,
  | 'activateTool'
  | 'externalEditorBusy'
  | 'placementCheck'
  | 'placementAreaDrawing'
  | 'pendingZone'
  | 'setSingleShrubSpecies'
  | 'setSingleTreeSpecies'
  | 'singleShrubSpecies'
  | 'singleTreeSpecies'
  | 'tool'
> {
  singlePlacement: Pick<
    WorkspaceReadyModel['singlePlacement'],
    | 'checking'
    | 'placing'
    | 'error'
    | 'notice'
    | 'needsRefresh'
    | 'refreshing'
    | 'retryRefresh'
  >;
  speciesQuery: Pick<WorkspaceReadyModel['speciesQuery'], 'data'>;
  hasPlan: boolean;
  pattern: ReactNode;
  brush: ReactNode;
  placement: ReactNode;
}

export const WorkspaceToolSlotPropsFor = (model: WorkspaceReadyModel) => ({
  activateTool: model.activateTool,
  externalEditorBusy: model.externalEditorBusy,
  placementCheck: model.placementCheck,
  placementAreaDrawing: model.placementAreaDrawing,
  pendingZone: model.pendingZone,
  setSingleShrubSpecies: model.setSingleShrubSpecies,
  setSingleTreeSpecies: model.setSingleTreeSpecies,
  singlePlacement: model.singlePlacement,
  singleShrubSpecies: model.singleShrubSpecies,
  singleTreeSpecies: model.singleTreeSpecies,
  speciesQuery: model.speciesQuery,
  tool: model.tool,
  hasPlan: Boolean(model.project.plan),
});

export const WorkspaceToolSlot: FC<WorkspaceToolSlotProps> = ({
  activateTool,
  externalEditorBusy,
  placementCheck,
  placementAreaDrawing,
  pendingZone,
  setSingleShrubSpecies,
  setSingleTreeSpecies,
  singlePlacement,
  singleShrubSpecies,
  singleTreeSpecies,
  speciesQuery,
  tool,
  hasPlan,
  pattern,
  brush,
  placement,
}) => {
  if (hasPlan && (tool === 'add_tree' || tool === 'add_shrub')) {
    return (
      <SinglePlacementPanel
        kind={tool === 'add_tree' ? 'tree' : 'shrub'}
        species={speciesQuery.data ?? []}
        speciesId={tool === 'add_tree' ? singleTreeSpecies : singleShrubSpecies}
        onSpeciesChange={
          tool === 'add_tree' ? setSingleTreeSpecies : setSingleShrubSpecies
        }
        check={placementCheck}
        checking={singlePlacement.checking}
        placing={singlePlacement.placing}
        disabled={externalEditorBusy}
        error={
          singlePlacement.error
            ? errorMessage(singlePlacement.error)
            : undefined
        }
        notice={singlePlacement.notice}
        needsRefresh={singlePlacement.needsRefresh}
        refreshing={singlePlacement.refreshing}
        onRefresh={() => void singlePlacement.retryRefresh()}
        onFinish={() => activateTool('select')}
      />
    );
  }
  if (hasPlan && tool === 'pattern_row') return pattern;
  if (hasPlan && tool === 'brush') return brush;
  if (
    hasPlan &&
    (tool === 'pattern_fill' ||
      (tool === 'draw_area' && placementAreaDrawing) ||
      pendingZone?.purpose === 'place')
  )
    return placement;
  return (
    <EditorPanel title="Инструмент">
      <p className="m-0 text-xs leading-4 text-neutral-600">
        Выберите инструмент на карте. Его параметры появятся здесь.
      </p>
    </EditorPanel>
  );
};
