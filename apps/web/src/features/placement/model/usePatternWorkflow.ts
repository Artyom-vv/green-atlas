import { useEffect, useRef, useState } from 'react';
import { useFormContext, useWatch } from 'react-hook-form';
import type {
  PlacementMaskPreset,
  SpeciesRevision,
  SpeciesShortlistItem,
} from '@green/api-client';
import { rowSketch, type RowAxis } from '@/entities/planting';
import {
  buildPatternDraft,
  toRowSketchSettings,
  type PatternDraft,
  type PatternFormValues,
  type PatternMode,
} from './patternForm';
import { usePatternSpecies } from './usePatternSpecies';

interface PatternWorkflowOptions {
  mode: PatternMode;
  guided?: boolean;
  species: SpeciesRevision[];
  shortlist?: SpeciesShortlistItem[];
  shortlistLoading?: boolean;
  placementMasks?: PlacementMaskPreset[];
  axis?: RowAxis;
  axisMode?: 'pick' | 'draw' | 'ready';
  selectedZoneIds?: string[];
  loading?: boolean;
  hasPreview: boolean;
  onPreview: (draft: PatternDraft) => void;
  onResetPreview?: () => void;
}

/** Wizard navigation and commands share the supplied RHF draft. Map geometry,
 * request concurrency and applying a preview remain owned by the workspace. */
export function usePatternWorkflow(options: PatternWorkflowOptions) {
  const form = useFormContext<PatternFormValues>();
  const values = useWatch({
    control: form.control,
    compute: (values) => values,
  });
  const { setValue } = form;
  const [step, setStep] = useState(0);
  const [catalog, setCatalog] = useState<'primary' | 'shrub'>();
  const bodyRef = useRef<HTMLDivElement>(null);
  const { guided, hasPreview, placementMasks, shortlistLoading } = options;
  useEffect(() => {
    if (guided) bodyRef.current?.focus({ preventScroll: true });
  }, [guided, step, catalog, hasPreview]);
  const species = usePatternSpecies({ ...options, values });
  useEffect(() => {
    if (values.placementScenario === 'natural' || !placementMasks) return;
    if (
      !placementMasks.find((item) => item.id === values.placementScenario)
        ?.available
    )
      setValue('placementScenario', 'natural');
  }, [placementMasks, setValue, values.placementScenario]);
  const sketch = rowSketch(options.axis, toRowSketchSettings(values));
  const canEditSettings =
    (options.selectedZoneIds?.length ?? 0) > 0 &&
    (options.mode !== 'row' ||
      (Boolean(options.axis) && options.axisMode !== 'draw'));
  const canPreview =
    canEditSettings && (options.mode !== 'row' || !sketch.invalidOffsets);
  const submit = () => {
    if (
      !canPreview ||
      !species.validSpecies ||
      options.loading ||
      shortlistLoading
    )
      return;
    const draft = buildPatternDraft(form.getValues(), {
      mode: options.mode,
      axis: options.axis,
      zoneIds: options.selectedZoneIds ?? [],
    });
    if (draft) options.onPreview(draft);
  };
  const editPreview = () => {
    setStep(2);
    options.onResetPreview?.();
  };
  const selectSpecies = (id: string) => {
    setValue(
      catalog === 'shrub' ? 'shrubSpeciesId' : species.primaryField,
      id,
      { shouldDirty: true },
    );
    setCatalog(undefined);
  };
  const selectAlternative = (id: string) => {
    setValue(species.primaryField, id, { shouldDirty: true });
    options.onResetPreview?.();
  };
  return {
    ...species,
    values,
    step,
    setStep,
    catalog,
    setCatalog,
    bodyRef,
    sketch,
    canEditSettings,
    canPreview,
    submit,
    editPreview,
    selectSpecies,
    selectAlternative,
  };
}
