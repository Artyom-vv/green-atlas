import {
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
  type Ref,
} from 'react';
import type {
  PlantingZoneAssignment,
  ValidationIssue,
} from '@green/api-client';
import type {
  SceneReviewHandle,
  SceneReviewOptions,
  SceneHoverPresentation,
} from '@/widgets/scene/model/sceneContracts';
import type { SceneController } from './SceneController';
import type { SceneRenderTelemetry } from '@/widgets/scene/model/sceneRenderContract';
import { createSceneSession } from './createSceneSession';

const EMPTY_ZONES: PlantingZoneAssignment[] = [];
const EMPTY_IDS: string[] = [];
const EMPTY_ISSUES: ValidationIssue[] = [];

/** Synchronizes React data with the canvas; the session owns all GPU resources. */
export function useSceneRenderer(
  options: SceneReviewOptions,
  ref?: Ref<SceneReviewHandle>,
) {
  const {
    snapshot,
    zones = EMPTY_ZONES,
    selectedZoneIds = EMPTY_IDS,
    selectedIds,
    issues = EMPTY_ISSUES,
    active = true,
    initialViewState,
  } = options;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const controllerRef = useRef<SceneController | undefined>(undefined);
  const [showRoots, setShowRoots] = useState(false);
  const [renderError, setRenderError] = useState<string>();
  const [modelsReady, setModelsReady] = useState(false);
  const [telemetry, setTelemetry] = useState<SceneRenderTelemetry>();
  const [hover, setHover] = useState<SceneHoverPresentation>();
  const [rendererAttempt, setRendererAttempt] = useState(0);
  const latest = useRef({ options, showRoots });
  useLayoutEffect(() => {
    latest.current = { options, showRoots };
  });

  useImperativeHandle(
    ref,
    () => ({
      getViewState: () => controllerRef.current?.exportPlanViewState(),
      fitPlantings: () => controllerRef.current?.fitPlantings(),
      fitSelection: () => controllerRef.current?.fitSelection(),
      fitExtent: (extent) => controllerRef.current?.fitExtent(extent),
    }),
    [],
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const session = createSceneSession(canvas, {
      initialViewState: latest.current.options.initialViewState,
      onSelect: (objectId) => latest.current.options.onSelect(objectId),
      onMoveTarget: (coordinate) =>
        latest.current.options.onMoveTarget?.(coordinate),
      onViewStateChange: (state) =>
        latest.current.options.onViewStateChange?.(state),
      onHover: (next) => {
        if (!next.objectId || latest.current.options.active === false) {
          setHover(undefined);
          return;
        }
        const rect = canvas.getBoundingClientRect();
        setHover({
          objectId: next.objectId,
          x: next.clientX - rect.left,
          y: next.clientY - rect.top,
        });
      },
      onTelemetry: setTelemetry,
      onReady: (controller) => {
        controllerRef.current = controller;
        const current = latest.current.options;
        if (current.snapshot)
          controller.updateSnapshot(
            current.snapshot,
            current.issues ?? EMPTY_ISSUES,
          );
        controller.updateSelection(current.selectedIds);
        controller.updateWorkZones(
          current.zones ?? EMPTY_ZONES,
          current.selectedZoneIds ?? EMPTY_IDS,
        );
        controller.setRootsVisible(latest.current.showRoots);
        if (current.active !== false && current.initialViewState)
          controller.importPlanViewState(current.initialViewState);
        setModelsReady(true);
        setRenderError(undefined);
      },
      onError: (message) => {
        controllerRef.current = undefined;
        setModelsReady(false);
        setRenderError(message);
      },
    });
    return () => {
      session.dispose();
      controllerRef.current = undefined;
    };
  }, [rendererAttempt]);

  useEffect(() => {
    if (snapshot)
      controllerRef.current?.updateSnapshot(
        snapshot,
        latest.current.options.issues ?? EMPTY_ISSUES,
      );
  }, [snapshot]);
  useEffect(() => {
    controllerRef.current?.updateIssues(issues);
  }, [issues]);
  useEffect(() => {
    controllerRef.current?.updateSelection(selectedIds);
  }, [selectedIds]);
  useEffect(() => {
    controllerRef.current?.updateWorkZones(zones, selectedZoneIds);
  }, [zones, selectedZoneIds, snapshot]);
  useEffect(() => {
    controllerRef.current?.setRootsVisible(showRoots);
  }, [showRoots]);
  useEffect(() => {
    if (active && initialViewState)
      controllerRef.current?.importPlanViewState(initialViewState);
  }, [active, initialViewState]);

  return {
    canvasRef,
    controllerRef,
    showRoots,
    setShowRoots,
    renderError,
    modelsReady,
    telemetry,
    hover: active ? hover : undefined,
    retry: () => {
      setRenderError(undefined);
      setModelsReady(false);
      setTelemetry(undefined);
      setRendererAttempt((attempt) => attempt + 1);
    },
  };
}
