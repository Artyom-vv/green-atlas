import { useEffect, useRef, useState } from 'react';
import { useWatch, type UseFormReturn } from 'react-hook-form';
import type {
  BuildingScreenTargets,
  PlanningBrief,
  PlantingZoneAssignment,
} from '@green/api-client';
import {
  buildBuildingScreenDraft,
  buildRecommendationDraft,
  type BuildingScreenDraft,
  type RecommendationDraft,
  type RecommendationFormValues,
} from './recommendationForm';
import { useRecommendationTask } from './useRecommendationTask';

export interface RecommendationNavigationOptions {
  zones: PlantingZoneAssignment[];
  guided?: boolean;
  active?: boolean;
  selectedZoneIds?: string[];
  onSelectedZoneIdsChange?: (ids: string[]) => void;
  onPreview: (draft: RecommendationDraft) => void;
  onCancel: () => void;
  onInterpret?: (task: string, signal: AbortSignal) => Promise<PlanningBrief>;
  onScreenMode?: (active: boolean) => void;
  onScreenPreview?: (draft: BuildingScreenDraft) => void;
  screenTargets?: BuildingScreenTargets;
  screenLoading?: boolean;
  screenError?: string;
}
const availableIds = (zones: PlantingZoneAssignment[]) =>
  zones.flatMap((zone) => (zone.id ? [zone.id] : []));

/** Owns task/zone navigation while previews and the persistent form stay external. */
export function useRecommendationNavigation(
  form: UseFormReturn<RecommendationFormValues>,
  options: RecommendationNavigationOptions,
) {
  const {
    zones,
    guided = false,
    active = true,
    onScreenMode,
    screenTargets,
    screenLoading,
    screenError,
  } = options;
  const values = useWatch({
    control: form.control,
    compute: (values) => values,
  });
  const task = useRecommendationTask({
    form,
    active,
    onInterpret: options.onInterpret,
    supportsBuildingScreen: Boolean(options.onScreenPreview),
  });
  const [zoneIds, setZoneIds] = useState(() => availableIds(zones));
  const [step, setStep] = useState(0);
  const taskInput = useRef<HTMLTextAreaElement | null>(null);
  const body = useRef<HTMLDivElement | null>(null);
  const textEntry = Boolean(options.onInterpret) && task.stage === 'text';
  const buildingScreen = task.stage === 'building_screen';
  const interpreted = task.stage === 'interpreted';
  const workZoneIds = options.selectedZoneIds ?? zoneIds;
  useEffect(() => {
    onScreenMode?.(active && buildingScreen);
    return () => onScreenMode?.(false);
  }, [active, buildingScreen, onScreenMode]);
  useEffect(() => {
    if (body.current) body.current.scrollTop = 0;
    if (textEntry && (!guided || step === 1)) taskInput.current?.focus();
  }, [step, textEntry, guided]);
  useEffect(() => {
    const available = new Set(availableIds(zones));
    setZoneIds((current) => {
      const retained = current.filter((id) => available.has(id));
      return retained.length ? retained : availableIds(zones);
    });
  }, [zones]);
  const back = () => {
    task.cancel();
    if (interpreted || buildingScreen) task.returnToText();
    else if (guided && step) setStep(0);
    else options.onCancel();
  };
  const next = () => {
    if (guided && !step) setStep(1);
    else if (textEntry) void task.interpret();
    else if (buildingScreen)
      options.onScreenPreview?.(
        buildBuildingScreenDraft(form.getValues(), workZoneIds),
      );
    else
      options.onPreview(
        buildRecommendationDraft(form.getValues(), workZoneIds),
      );
  };
  const canContinue =
    workZoneIds.length > 0 &&
    !(
      buildingScreen &&
      (!screenTargets?.geometry ||
        screenLoading ||
        screenError ||
        (values.screenSide === 'roads' && !screenTargets.has_roads))
    ) &&
    !((!guided || step) && textEntry && values.task.trim().length < 5);
  return {
    task,
    step,
    body,
    taskInput,
    textEntry,
    buildingScreen,
    interpreted,
    workZoneIds,
    back,
    next,
    canContinue,
    onZonesChange: options.onSelectedZoneIdsChange ?? setZoneIds,
  };
}
