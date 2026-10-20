import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type RefObject,
} from 'react';
import { useQuery } from '@tanstack/react-query';
import type { Project } from '@green/api-client';
import { useEditorSession } from '@/entities/editor';
import type { PlanViewState } from '@/entities/editor/model/planViewState';
import { workspaceSceneQuery } from '@/features/workspace/api/workspaceQueries';
import type { MapViewportHandle } from '@/widgets/map/model/mapContracts';
import type { SceneReviewHandle } from '@/widgets/scene/model/sceneContracts';

const SCENE_HORIZON_DEBOUNCE_MS = 160;

interface WorkspaceSceneSessionOptions {
  projectId: string;
  project: Project | undefined;
  horizon: number;
  mapViewport: RefObject<Pick<
    MapViewportHandle,
    'getViewState' | 'applyViewState'
  > | null>;
  onBeforeOpen: () => void;
}

/** Owns the mounted project's camera bridge and scene read lifecycle. */
export function useWorkspaceSceneSession({
  projectId,
  project,
  horizon,
  mapViewport,
  onBeforeOpen,
}: WorkspaceSceneSessionOptions) {
  const scenePhase = useEditorSession((state) => state.scenePhase);
  const sceneInitialViewState = useEditorSession(
    (state) => state.sceneInitialViewState,
  );
  const showScene = useEditorSession((state) => state.showScene);
  const hideScene = useEditorSession((state) => state.hideScene);
  const sceneReview = useRef<SceneReviewHandle>(null);
  const sceneViewStateRef = useRef<PlanViewState | undefined>(undefined);
  const sceneOpen = scenePhase === 'visible';
  const [sceneRequestHorizon, setSceneRequestHorizon] = useState(0);
  useEffect(() => {
    const timer = window.setTimeout(
      () => setSceneRequestHorizon(horizon),
      SCENE_HORIZON_DEBOUNCE_MS,
    );
    return () => window.clearTimeout(timer);
  }, [horizon]);

  const changeMapMode = useCallback(
    (mode: '2d' | '3d') => {
      if (mode === '3d') {
        onBeforeOpen();
        const state = mapViewport.current?.getViewState();
        if (state) sceneViewStateRef.current = state;
        showScene(state);
        return;
      }
      const state =
        sceneReview.current?.getViewState() ?? sceneViewStateRef.current;
      if (state) {
        sceneViewStateRef.current = state;
        mapViewport.current?.applyViewState(state);
      }
      // The latest 3D camera also becomes the next activation's snapshot.
      hideScene(state);
    },
    [hideScene, mapViewport, onBeforeOpen, showScene],
  );

  const sceneQuery = useQuery(
    workspaceSceneQuery(
      projectId,
      project,
      sceneRequestHorizon,
      sceneOpen && sceneRequestHorizon === horizon,
    ),
  );

  return {
    changeMapMode,
    sceneHorizon: horizon,
    sceneInitialViewState,
    // Keep the activated renderer and camera, but prepare new revisions only
    // while visible. Hidden 2D edits are read together on the next 3D opening.
    sceneMounted: scenePhase !== 'unmounted',
    sceneOpen,
    sceneQuery,
    sceneRequestHorizon,
    sceneReview,
    sceneViewStateRef,
  };
}
