import { useEffect, useRef, useState } from 'react';
import {
  api,
  type PatternPreview,
  type PatternPreviewRequest,
  type PlacementCheck,
  type PlanObjectCreate,
  type Project,
  type ChangeSetPreview,
} from '@green/api-client';

type Skipped = NonNullable<PatternPreview['skipped']>[number];
type Coordinate = [number, number];
export type InspectionPlant = Pick<PlanObjectCreate, 'kind' | 'layout_radius_m' | 'size_class' | 'species_revision_id' | 'spacing_policy'>;
type InspectionMarker = {
  coordinate: Coordinate;
  radius: number;
  status: 'allowed' | 'blocked' | 'unknown';
};

/** Read-only correction preview. Never places a tree on a map click. */
export function useCandidateInspection(options: {
  project: Project | undefined;
  preview: PatternPreview | undefined;
  request?: PatternPreviewRequest;
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
  const [diagnosing, setDiagnosing] = useState(false);
  const [diagnosis, setDiagnosis] = useState<PlacementCheck>();
  const pending = useRef<AbortController | undefined>(undefined);
  const inspectionPlant = useRef<InspectionPlant | undefined>(undefined);
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
    setDiagnosing(false);
    setDiagnosis(undefined);
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
    if (diagnosing) {
      if (!project?.id || !project.plan || !inspectionPlant.current) return;
      pending.current?.abort();
      const controller = new AbortController();
      pending.current = controller;
      const plant = inspectionPlant.current;
      const radius = plant.layout_radius_m ?? (plant.kind === 'shrub' ? 0.65 : 1.6);
      setChecking(true);
      setPicking(false);
      setDiagnosis(undefined);
      setError(undefined);
      setMarker({ coordinate, radius, status: 'unknown' });
      try {
        const reply = await api.checkPlacement(project.id, {
          ...plant,
          x: coordinate[0], y: coordinate[1],
          layout_radius_m: radius,
          base_plan_version: project.plan.version,
          state_version: project.state_version,
          geometry_version: project.geometry_version,
          explain_geometry: true,
        }, controller.signal);
        if (controller.signal.aborted || pending.current !== controller) return;
        setDiagnosis(reply);
        const state = reply.geometry_evidence?.state;
        const outsideSelection = !!reply.zone_id && !options.zoneIds.includes(reply.zone_id);
        if (outsideSelection) setError('Позиция вне выбранных рабочих участков');
        setMarker({ coordinate, radius, status: outsideSelection || reply.status === 'blocked' || state === 'excluded'
          ? 'blocked' : state === 'available' && reply.status === 'allowed' ? 'allowed' : 'unknown' });
      } catch (failure) {
        if (!controller.signal.aborted && pending.current === controller)
          setError(failure instanceof Error ? failure.message : 'Не удалось проверить позицию');
      } finally {
        if (pending.current === controller) setChecking(false);
      }
      return;
    }
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
      // Accepted neighbours from the current mass proposal are included, not
      // just saved plants. Ordinary pattern guards also keep local unknowns out.
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
    diagnosis,
    diagnosing,
    canExplain: !!project?.plan,
    startExplaining: (plant?: InspectionPlant) => {
      reset();
      const request = options.request;
      inspectionPlant.current = plant ?? (request ? {
        kind: request.plant_kind, layout_radius_m: request.layout_radius_m,
        size_class: request.size_class, spacing_policy: request.spacing_policy,
        species_revision_id: request.type !== 'row' && request.composition === 'mixed'
          ? request.tree_species_revision_id : request.species_revision_id,
      } : undefined);
      if (!inspectionPlant.current) return;
      setDiagnosing(true);
      setPicking(true);
    },
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
