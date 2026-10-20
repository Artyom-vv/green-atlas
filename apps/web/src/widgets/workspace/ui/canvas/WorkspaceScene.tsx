import { errorMessage as message } from '@/shared/errors/errorMessage';
import { useSceneActivity } from '@/widgets/scene/model/SceneActivityContext';
import { ViewportLoading } from '@/shared/ui/ViewportLoading';
import {
  CanvasControlGroup,
  CanvasControlSlot,
} from '@/shared/ui/CanvasControls';
import type { FC, ReactNode } from 'react';
import { lazy, Suspense } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';
const SceneReview = lazy(async () => ({
  default: (await import('@/widgets/scene/ui/SceneReview')).SceneReview,
}));
export interface WorkspaceSceneProps extends Pick<
  WorkspaceReadyModel,
  | 'editorBusy'
  | 'focusZones'
  | 'handleCoordinate'
  | 'issues'
  | 'mapPanActive'
  | 'planLocked'
  | 'project'
  | 'projectHasPlan'
  | 'sceneHorizon'
  | 'sceneInitialViewState'
  | 'sceneMounted'
  | 'sceneOpen'
  | 'sceneQuery'
  | 'sceneRequestHorizon'
  | 'sceneReview'
  | 'sceneViewStateRef'
  | 'selectFromExplorer'
  | 'selectedIds'
  | 'selectedLocked'
  | 'selectedPatternZoneIds'
  | 'selectedPatternZones'
  | 'setSceneHorizon'
> {
  viewControls?: ReactNode;
  mapScenarioIssues?: WorkspaceReadyModel['mapScenarioIssues'];
}
export const WorkspaceScenePropsFor = (props: WorkspaceSceneProps) => ({
  editorBusy: props.editorBusy,
  focusZones: props.focusZones,
  handleCoordinate: props.handleCoordinate,
  issues: props.issues,
  mapScenarioIssues: props.mapScenarioIssues,
  mapPanActive: props.mapPanActive,
  planLocked: props.planLocked,
  project: props.project,
  projectHasPlan: props.projectHasPlan,
  sceneHorizon: props.sceneHorizon,
  sceneInitialViewState: props.sceneInitialViewState,
  sceneMounted: props.sceneMounted,
  sceneOpen: props.sceneOpen,
  sceneQuery: props.sceneQuery,
  sceneRequestHorizon: props.sceneRequestHorizon,
  sceneReview: props.sceneReview,
  sceneViewStateRef: props.sceneViewStateRef,
  selectFromExplorer: props.selectFromExplorer,
  selectedIds: props.selectedIds,
  selectedLocked: props.selectedLocked,
  selectedPatternZoneIds: props.selectedPatternZoneIds,
  selectedPatternZones: props.selectedPatternZones,
  setSceneHorizon: props.setSceneHorizon,
});
export const WorkspaceScene: FC<WorkspaceSceneProps> = ({
  editorBusy,
  focusZones,
  handleCoordinate,
  issues,
  mapScenarioIssues,
  mapPanActive,
  planLocked,
  project,
  projectHasPlan,
  sceneHorizon,
  sceneInitialViewState,
  sceneMounted,
  sceneOpen,
  sceneQuery,
  sceneRequestHorizon,
  sceneReview,
  sceneViewStateRef,
  selectFromExplorer,
  selectedIds,
  selectedLocked,
  selectedPatternZoneIds,
  selectedPatternZones,
  setSceneHorizon,
  viewControls,
}) => (
  <>
    {sceneMounted && (
      <Suspense
        fallback={
          <div
            className="hidden:hidden pointer-events-none absolute inset-0 z-30"
            hidden={!sceneOpen}
          >
            <SceneLoadingMarker active={sceneOpen} />
            <CanvasControlSlot position="topRight">
              <CanvasControlGroup label="Отображение карты">
                {viewControls}
              </CanvasControlGroup>
            </CanvasControlSlot>
          </div>
        }
      >
        <SceneReview
          nativeModels
          showGrowthControl={false}
          viewControls={viewControls}
          onFitZones={
            selectedPatternZones.length
              ? () => focusZones(selectedPatternZones)
              : undefined
          }
          zoneCount={selectedPatternZones.length}
          ref={sceneReview}
          active={sceneOpen}
          zones={project.planting_zones ?? []}
          selectedZoneIds={selectedPatternZoneIds}
          snapshot={sceneQuery.data}
          displayEnvironment={sceneQuery.data?.displayEnvironment}
          horizon={sceneHorizon}
          selectedIds={selectedIds}
          issues={mapScenarioIssues ?? issues}
          loading={
            sceneHorizon !== sceneRequestHorizon ||
            sceneQuery.isLoading ||
            sceneQuery.isFetching
          }
          error={
            !sceneQuery.isFetching && sceneQuery.error
              ? message(sceneQuery.error)
              : undefined
          }
          onRetryScene={() => void sceneQuery.refetch()}
          initialViewState={sceneInitialViewState}
          onViewStateChange={(state) => {
            sceneViewStateRef.current = state;
          }}
          onHorizon={setSceneHorizon}
          onSelect={(id) => {
            if (!mapPanActive) selectFromExplorer([id], false);
          }}
          onMoveTarget={
            projectHasPlan && !planLocked && !editorBusy && !selectedLocked
              ? handleCoordinate
              : undefined
          }
        />
      </Suspense>
    )}
  </>
);

function SceneLoadingMarker({ active }: { active: boolean }) {
  const activity = useSceneActivity();
  if (!active) return null;
  return <ViewportLoading label="Открываем 3D" {...activity} />;
}
