import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { useWatch, type UseFormReturn } from 'react-hook-form';
import type { PlanningBrief } from '@green/api-client';
import {
  resolvePlanningTask,
  type RecommendationFormValues,
} from './recommendationForm';

export type RecommendationTaskStage =
  'text' | 'manual' | 'interpreted' | 'building_screen';
export interface RecommendationTaskOptions {
  form: UseFormReturn<RecommendationFormValues>;
  active: boolean;
  onInterpret?: (task: string, signal: AbortSignal) => Promise<PlanningBrief>;
  supportsBuildingScreen: boolean;
}
interface PendingTask {
  id: number;
  controller: AbortController;
}

/** Owns only text interpretation. Spatial preview and application stay with the caller. */
export function useRecommendationTask(options: RecommendationTaskOptions) {
  const { form, active } = options;
  const { control, subscribe, getValues } = form;
  const latest = useRef(options);
  useLayoutEffect(() => {
    latest.current = options;
  }, [options]);
  const mounted = useRef(true);
  const sequence = useRef(0);
  const pending = useRef<PendingTask | undefined>(undefined);
  const [stage, setStage] = useState<RecommendationTaskStage>(
    options.onInterpret ? 'text' : 'manual',
  );
  const [interpreting, setInterpreting] = useState(false);
  const [feedback, setFeedback] = useState<string[]>([]);
  const [requiresComposition, setRequiresComposition] = useState(false);
  const task = useWatch({ control: form.control, name: 'task' });

  const invalidate = useCallback(() => {
    sequence.current += 1;
    pending.current?.controller.abort();
    pending.current = undefined;
  }, []);
  const cancel = useCallback(() => {
    invalidate();
    if (mounted.current) setInterpreting(false);
  }, [invalidate]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      invalidate();
    };
  }, [invalidate]);
  useEffect(() => {
    let previous = getValues('task');
    return subscribe({
      name: 'task',
      exact: true,
      formState: { values: true },
      callback: ({ values }) => {
        if (values.task !== previous) {
          previous = values.task;
          invalidate();
        }
      },
    });
  }, [control, subscribe, getValues, invalidate]);
  useEffect(() => {
    cancel();
    setFeedback([]);
    setRequiresComposition(false);
  }, [task, control, cancel]);
  useEffect(() => {
    if (!active) cancel();
  }, [active, cancel]);

  const manualEntry = useCallback(() => {
    cancel();
    setStage('manual');
    setFeedback([]);
    setRequiresComposition(false);
  }, [cancel]);
  const returnToText = useCallback(() => {
    cancel();
    setStage('text');
  }, [cancel]);
  const interpret = useCallback(async () => {
    const captured = latest.current;
    if (!captured.active || !captured.onInterpret) return;
    cancel();
    const originalTask = captured.form.getValues('task');
    const request = {
      id: ++sequence.current,
      controller: new AbortController(),
    };
    pending.current = request;
    const current = () =>
      mounted.current &&
      latest.current.active &&
      latest.current.form.control === captured.form.control &&
      sequence.current === request.id &&
      pending.current === request &&
      !request.controller.signal.aborted &&
      captured.form.getValues('task') === originalTask;
    setInterpreting(true);
    setFeedback([]);
    setRequiresComposition(false);
    try {
      const brief = await captured.onInterpret(
        originalTask.trim(),
        request.controller.signal,
      );
      if (!current()) return;
      const result = resolvePlanningTask(
        brief,
        captured.supportsBuildingScreen,
      );
      if (result.kind === 'feedback') {
        setFeedback(result.messages);
        setRequiresComposition(result.requiresComposition);
      } else if (result.kind === 'building_screen') {
        captured.form.setValue('screenLimit', result.maxSites, {
          shouldDirty: true,
        });
        setStage('building_screen');
      } else {
        captured.form.setValue('profile', result.profile, {
          shouldDirty: true,
        });
        captured.form.setValue('maxSites', result.maxSites, {
          shouldDirty: true,
        });
        setStage('interpreted');
      }
    } catch (cause) {
      if (current())
        setFeedback([
          cause instanceof Error
            ? cause.message
            : 'Не удалось разобрать задание.',
        ]);
    } finally {
      if (current()) {
        pending.current = undefined;
        setInterpreting(false);
      }
    }
  }, [cancel]);
  return {
    stage,
    interpreting,
    feedback,
    requiresComposition,
    cancel,
    manualEntry,
    returnToText,
    interpret,
  };
}
