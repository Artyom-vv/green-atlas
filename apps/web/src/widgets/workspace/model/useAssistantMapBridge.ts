import type { AssistantContextValue } from '@/features/assistant/model/assistantContext';
import {
  currentControlGeometry,
  type AgentMapControl,
} from '@/features/assistant/model/autonomous/autonomousControl';
import type { MapViewportHandle } from '@/widgets/map/model/mapContracts';
import type { Project } from '@green/api-client';
import { useLayoutEffect, useRef, type RefObject } from 'react';

export interface AssistantMapBridgeOptions {
  projectId: string;
  project: Project | undefined;
  sceneOpen: boolean;
  mapViewportRef: RefObject<Pick<MapViewportHandle, 'focusGeometry'> | null>;
  registerMapControl: AssistantContextValue['registerMapControl'];
}

/** The bridge only exposes committed workspace state to external commands. */
export function useAssistantMapBridge({
  projectId,
  project,
  sceneOpen,
  mapViewportRef,
  registerMapControl,
}: AssistantMapBridgeOptions) {
  const workspace = useRef({ projectId, project, sceneOpen });
  const pending = useRef<AbortController | undefined>(undefined);

  useLayoutEffect(() => {
    workspace.current = { projectId, project, sceneOpen };
  }, [projectId, project, sceneOpen]);

  useLayoutEffect(() => {
    let registered = true;
    const adapter: AgentMapControl = async (command, geometry, signal) => {
      const current = workspace.current;
      if (
        current.projectId !== projectId ||
        command.project_id !== current.projectId ||
        !currentControlGeometry(command, geometry, current.project)
      )
        return { status: 'failed', error_code: 'CONTROL_STALE' };

      const viewport = mapViewportRef.current;
      if (!registered || current.sceneOpen || !viewport)
        return { status: 'failed', error_code: 'MAP_UNAVAILABLE' };

      const operation = new AbortController();
      pending.current?.abort();
      pending.current = operation;
      const abort = () => operation.abort();
      signal.addEventListener('abort', abort, { once: true });
      if (signal.aborted) operation.abort();
      try {
        const result = await viewport.focusGeometry(
          geometry,
          command.geometry_version,
          operation.signal,
        );
        if (
          workspace.current.projectId !== projectId ||
          !currentControlGeometry(command, geometry, workspace.current.project)
        )
          return { status: 'failed', error_code: 'CONTROL_STALE' };
        if (operation.signal.aborted)
          return { status: 'cancelled', error_code: 'FOCUS_INTERRUPTED' };
        return result;
      } finally {
        signal.removeEventListener('abort', abort);
        if (pending.current === operation) pending.current = undefined;
      }
    };
    const unregister = registerMapControl?.(adapter);
    return () => {
      registered = false;
      pending.current?.abort();
      unregister?.();
    };
  }, [projectId, mapViewportRef, registerMapControl]);

  useLayoutEffect(
    () => () => pending.current?.abort(),
    [projectId, project?.geometry_version, sceneOpen],
  );
}
