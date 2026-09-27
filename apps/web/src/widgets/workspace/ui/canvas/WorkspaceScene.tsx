import { errorMessage as message } from '@/shared/errors/errorMessage';
import { Progress } from '@green/ui';
import type { FC } from 'react';
import { lazy, Suspense } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';
const SceneReview = lazy(async () => ({
  default: (await import('@/widgets/scene/ui/SceneReview')).SceneReview,
}));
export interface WorkspaceSceneProps extends Pick<
  WorkspaceReadyModel,
  | 'editorBusy'
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
  | 'setSceneHorizon'
> {}
export const WorkspaceScenePropsFor = (props: WorkspaceSceneProps) => ({
  editorBusy: props.editorBusy,
  handleCoordinate: props.handleCoordinate,
  issues: props.issues,
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
  setSceneHorizon: props.setSceneHorizon,
});
export const WorkspaceScene: FC<WorkspaceSceneProps> = ({
  editorBusy,
  handleCoordinate,
  issues,
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
  setSceneHorizon,
}) => (
  <>
    {sceneMounted && (
      <Suspense
        fallback={
          <div
            className="hidden:hidden absolute inset-0 z-30 grid place-items-center bg-neutral-100"
            hidden={!sceneOpen}
          >
            <Progress label="Загрузка 3D" />
          </div>
        }
      >
        <SceneReview
          showGrowthControl
          ref={sceneReview}
          active={sceneOpen}
          zones={project.planting_zones ?? []}
          selectedZoneIds={selectedPatternZoneIds}
          snapshot={sceneQuery.data}
          horizon={sceneHorizon}
          selectedIds={selectedIds}
          issues={issues}
          loading={
            sceneHorizon !== sceneRequestHorizon ||
            sceneQuery.isLoading ||
            sceneQuery.isFetching
          }
          error={sceneQuery.error ? message(sceneQuery.error) : undefined}
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
