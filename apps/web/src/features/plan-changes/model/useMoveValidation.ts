import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import type {
  ChangeSetPreview,
  PlanChangeSetDraft,
  PlanObject,
} from '@green/api-client';
import { groupTransformDraft } from '@/entities/planting/model/groupTransform';
import { useLatestPreview } from '@/shared/async/useLatestPreview';
import { previewPlanChanges } from '../api/previewPlanChanges';
import {
  moveValidationFromPreview,
  type MoveLiveValidation,
} from './moveLiveValidation';

const MOVE_CHECK_DELAY_MS = 120;

export interface MoveValidationOptions {
  projectId: string;
  planVersion?: number;
  stateVersion?: number;
  geometryVersion?: number;
  objects: PlanObject[];
  active: boolean;
}

/** Pointer validation shares preview cancellation; it never applies a change. */
export function useMoveValidation(options: MoveValidationOptions) {
  const {
    projectId,
    planVersion,
    stateVersion,
    geometryVersion,
    objects,
    active,
  } = options;
  const scopeKey = JSON.stringify([
    projectId,
    planVersion,
    stateVersion,
    geometryVersion,
    objects.map((object) => object.id),
    active,
  ]);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [scheduledScope, setScheduledScope] = useState<string>();
  const preview = useLatestPreview<PlanChangeSetDraft, ChangeSetPreview>({
    scopeKey,
    execute: (draft, signal) => previewPlanChanges(projectId, draft, signal),
  });
  const reset = preview.reset;
  const clear = useCallback(() => {
    clearTimeout(timer.current);
    timer.current = undefined;
    setScheduledScope(undefined);
    reset();
  }, [reset]);
  useLayoutEffect(() => {
    clearTimeout(timer.current);
    timer.current = undefined;
    setScheduledScope(undefined);
  }, [scopeKey]);
  useEffect(() => () => clearTimeout(timer.current), []);
  const check = (coordinate?: [number, number]) => {
    clear();
    if (!active || !coordinate || planVersion === undefined || !objects.length)
      return;
    const draft = groupTransformDraft(planVersion, objects, 'move', coordinate);
    if (!draft) return;
    setScheduledScope(scopeKey);
    timer.current = setTimeout(() => {
      timer.current = undefined;
      setScheduledScope(undefined);
      preview.mutate(draft);
    }, MOVE_CHECK_DELAY_MS);
  };
  let validation: MoveLiveValidation | undefined;
  if (scheduledScope === scopeKey || preview.isPending) {
    validation = {
      status: 'checking',
      reason: 'Проверяем новое положение',
      objectStatuses: {},
    };
  } else if (preview.data) {
    validation = moveValidationFromPreview(preview.data);
  } else if (preview.error) {
    validation = {
      status: 'unknown',
      reason: 'Не удалось проверить положение',
      objectStatuses: {},
    };
  }
  return { check, clear, validation };
}
