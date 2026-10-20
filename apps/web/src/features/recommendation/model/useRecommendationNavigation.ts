import { useEffect, useRef, useState } from 'react';
import type { UseFormReturn } from 'react-hook-form';
import type { PlantingZoneAssignment } from '@green/api-client';
import {
  buildRecommendationDraft,
  type RecommendationDraft,
  type RecommendationFormValues,
} from './recommendationForm';

export interface RecommendationNavigationOptions {
  zones: PlantingZoneAssignment[];
  guided?: boolean;
  selectedZoneIds?: string[];
  onSelectedZoneIdsChange?: (ids: string[]) => void;
  onPreview: (draft: RecommendationDraft) => void;
  onCancel: () => void;
}
const availableIds = (zones: PlantingZoneAssignment[]) =>
  zones.flatMap((zone) => (zone.id ? [zone.id] : []));

/** Owns zone navigation while previews and the persistent form stay external. */
export function useRecommendationNavigation(
  form: UseFormReturn<RecommendationFormValues>,
  options: RecommendationNavigationOptions,
) {
  const { zones, guided = false } = options;
  const [zoneIds, setZoneIds] = useState(() => availableIds(zones));
  const [step, setStep] = useState(0);
  const body = useRef<HTMLDivElement | null>(null);
  const workZoneIds = options.selectedZoneIds ?? zoneIds;
  useEffect(() => {
    if (body.current) body.current.scrollTop = 0;
  }, [step]);
  useEffect(() => {
    const available = new Set(availableIds(zones));
    setZoneIds((current) => {
      const retained = current.filter((id) => available.has(id));
      return retained.length ? retained : availableIds(zones);
    });
  }, [zones]);
  const back = () => {
    if (guided && step) setStep(0);
    else options.onCancel();
  };
  const next = () => {
    if (guided && !step) setStep(1);
    else
      options.onPreview(
        buildRecommendationDraft(form.getValues(), workZoneIds),
      );
  };
  return {
    step,
    body,
    workZoneIds,
    back,
    next,
    canContinue: workZoneIds.length > 0,
    onZonesChange: options.onSelectedZoneIdsChange ?? setZoneIds,
  };
}
