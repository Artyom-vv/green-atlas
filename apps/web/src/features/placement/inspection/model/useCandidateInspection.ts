import { useEffect, useRef, useState } from 'react';
import {
  api,
  type PatternPreview,
  type Project,
  type ChangeSetPreview,
} from '@green/api-client';

type Skipped = NonNullable<PatternPreview['skipped']>[number];
type Coordinate = [number, number];
type InspectionMarker = {
  coordinate: Coordinate;
  radius: number;
  status: 'allowed' | 'blocked' | 'unknown';
};

/** Проверяет перенос отклонённого кандидата вместе с остальными посадками варианта. */
export function useCandidateInspection(options: {
  project: Project | undefined;
  preview: PatternPreview | undefined;
  zoneIds: string[];
  onFocus: (coordinate: Coordinate) => void;
  onApply: (preview: ChangeSetPreview) => void;
}) {
  const [selected, setSelected] = useState<{
    candidate: Skipped;
    index: number;
  }>();
  const [marker, setMarker] = useState<InspectionMarker>();
  const [picking, setPicking] = useState(false);
  const [checking, setChecking] = useState(false);
  const [trial, setTrial] = useState<ChangeSetPreview>();
  const [error, setError] = useState<string>();
  const pending = useRef<AbortController | undefined>(undefined);
  const { project, preview } = options;
  const reset = () => {
    pending.current?.abort();
    pending.current = undefined;
    setSelected(undefined);
    setMarker(undefined);
    setPicking(false);
    setChecking(false);
    setTrial(undefined);
    setError(undefined);
  };
  useEffect(() => {
    reset();
    return () => pending.current?.abort();
  }, [preview, project?.state_version]);

  const select = (candidate: Skipped, index: number) => {
    reset();
    setSelected({ candidate, index });
    const coordinate: Coordinate = [candidate.x, candidate.y];
    setMarker({
      coordinate,
      radius:
        candidate.candidate?.layout_radius_m ??
        candidate.candidate?.radius ??
        (candidate.candidate?.kind === 'shrub' ? 0.65 : 1.6),
      status: candidate.status === 'blocked' ? 'blocked' : 'unknown',
    });
    options.onFocus(coordinate);
  };
  const probe = async (coordinate: Coordinate) => {
    if (
      !project?.id ||
      !project.plan ||
      !preview ||
      !selected?.candidate.candidate
    )
      return;
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    setChecking(true);
    setPicking(false);
    setTrial(undefined);
    setError(undefined);
    setMarker(
      (previous) => previous && { ...previous, coordinate, status: 'unknown' },
    );
    try {
      // Проверка учитывает уже принятых соседей из текущего варианта,
      // а общие правила применения не пропускают неуточнённые позиции.
      const reply = await api.previewPlanChanges(
        project.id,
        {
          base_plan_version: project.plan.version,
          source: 'pattern',
          policy: 'all_or_nothing',
          label: 'Уточнение позиции',
          operations: [
            ...(preview.change_set?.additions ?? []).map((object) => ({
              type: 'add' as const,
              object,
            })),
            {
              type: 'add',
              object: {
                ...selected.candidate.candidate,
                x: coordinate[0],
                y: coordinate[1],
              },
            },
          ],
        },
        controller.signal,
      );
      if (controller.signal.aborted || pending.current !== controller) return;
      const result = reply.candidate_results?.at(-1);
      if (result?.zone_id && !options.zoneIds.includes(result.zone_id)) {
        setError('Позиция вне выбранных рабочих участков');
        setMarker((previous) => previous && { ...previous, status: 'blocked' });
        return;
      }
      setTrial(reply);
      setMarker(
        (previous) =>
          previous && {
            ...previous,
            status: reply.can_apply
              ? 'allowed'
              : result?.status === 'blocked'
                ? 'blocked'
                : 'unknown',
          },
      );
    } catch (failure) {
      if (!controller.signal.aborted)
        setError(
          failure instanceof Error
            ? failure.message
            : 'Не удалось проверить позицию',
        );
    } finally {
      if (pending.current === controller) setChecking(false);
    }
  };
  return {
    selected,
    marker,
    picking,
    checking,
    trial,
    error,
    select,
    reset,
    probe,
    startPicking: () => {
      setPicking(true);
      setError(undefined);
    },
    cancelPicking: () => setPicking(false),
    apply: () => {
      if (trial?.can_apply && !checking && !picking && !error)
        options.onApply(trial);
    },
  };
}

export type CandidateInspection = ReturnType<typeof useCandidateInspection>;
