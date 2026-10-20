import { useCallback, useEffect, useState, type RefObject } from 'react';
import { useWatch } from 'react-hook-form';
import type { MapTool } from '@/entities/editor';
import { placementPreviewFrame } from '@/entities/planting/model/placementPreviewFrame';
import {
  toRowSketchSettings,
  usePatternForm,
  type PatternFormValues,
} from '@/features/placement';
import { toLiveBrushSettings } from '@/features/placement/brush/model/brushForm';
import { useBrushForm } from '@/features/placement/brush/model/useBrushForm';
import { useRecommendationForm } from '@/features/recommendation';
import { previewPlanChanges } from '@/features/plan-changes/api/previewPlanChanges';
import {
  previewWorkspaceBrush,
  previewWorkspaceRecommendation,
  previewWorkspaceAutomatic,
} from '@/features/workspace/api/workspacePreviews';
import { useLatestPreview } from '@/shared/async/useLatestPreview';
import { usePatternPreview } from '@/features/placement/pattern/model/usePatternPreview';
import type { MapViewportHandle } from '@/widgets/map/model/mapContracts';
import type {
  Project,
  PlanObject,
  BuildingScreenRequest,
  BrushPreview,
  AutomaticPlacementPreview,
  AutomaticPlacementRequest,
  BrushPreviewRequest,
  BrushStroke,
  ChangeSetPreview,
  PlanChangeSetDraft,
  RecommendationPreview,
  RecommendationRequest,
} from '@green/api-client';

interface PlacementDraftOptions {
  projectId: string;
  project?: Project;
  tool: MapTool;
  mapViewport: RefObject<Pick<MapViewportHandle, 'fitGeometry'> | null>;
  onSuggestionAccepted: () => void;
  onChangesAccepted: () => void;
}

/** Формы владеют черновиками, предпросмотры — отменой и проверкой ревизий.
 * Рабочая область выполняет переход только после завершённого предложения */
export function useWorkspacePlacementDraft({
  projectId,
  project,
  tool,
  mapViewport,
  onSuggestionAccepted,
  onChangesAccepted,
}: PlacementDraftOptions) {
  const focusAdditions = (objects: PlanObject[]) => {
    const frame = placementPreviewFrame(objects);
    if (frame) mapViewport.current?.fitGeometry(frame);
  };
  const [rowAxis, setRowAxis] = useState<{
    type: 'LineString';
    coordinates: number[][];
  }>();
  const [rowInputMode, setRowInputMode] = useState<'pick' | 'draw' | 'ready'>(
    'pick',
  );
  const [rowDrawingPoints, setRowDrawingPoints] = useState(0);
  const rowForm = usePatternForm();
  const fillForm = usePatternForm();
  const recommendationForm = useRecommendationForm();
  const recommendationValues = useWatch({
    control: recommendationForm.control,
  });
  const rowValues = useWatch({
    control: rowForm.control,
    compute: (values: PatternFormValues) => values,
  });
  const fillValues = useWatch({
    control: fillForm.control,
    compute: (values: PatternFormValues) => values,
  });
  const patternForm = tool === 'pattern_row' ? rowForm : fillForm;
  const patternValues = tool === 'pattern_row' ? rowValues : fillValues;
  const rowSettings = toRowSketchSettings(rowValues);
  const [rowAxisSource, setRowAxisSource] = useState<{
    type: 'dxf' | 'manual';
    label: string;
  }>();
  const [selectedPatternZoneIds, setSelectedPatternZoneIds] = useState<
    string[]
  >([]);
  const [recommendationOpen, setRecommendationOpen] = useState(false);
  const [brushStrokes, setBrushStrokes] = useState<BrushStroke[]>([]);
  const [brushWidth, setBrushWidth] = useState(12);
  const [brushOperation, setBrushOperation] = useState<'add' | 'subtract'>(
    'add',
  );
  const brushForm = useBrushForm();
  const brushSettings = useWatch({
    control: brushForm.control,
    compute: toLiveBrushSettings,
  });
  const [brushDrawing, setBrushDrawing] = useState(false);
  const [speciesAssignmentOpen, setSpeciesAssignmentOpen] = useState(false);
  const [speciesCatalogBrowsing, setSpeciesCatalogBrowsing] = useState(true);
  const [singleTreeSpecies, setSingleTreeSpecies] = useState<string>();
  const [singleShrubSpecies, setSingleShrubSpecies] = useState<string>();
  const [patternSettingsOpen, setPatternSettingsOpen] = useState(false);
  const projectBasis = [
    projectId,
    project?.plan?.version,
    project?.geometry_version,
    project?.state_version,
  ];
  const patternDraftKey = (values: PatternFormValues) =>
    JSON.stringify([
      tool,
      {
        ...values,
        planningHorizonYear: values.planningHorizonYear ?? 0,
      },
      rowAxis,
      selectedPatternZoneIds,
    ]);
  const previewPattern = usePatternPreview({
    projectId,
    project,
    tool,
    draftKey: patternDraftKey(patternValues),
    getDraftKey: () => patternDraftKey(patternForm.getValues()),
    getYear: () => patternForm.getValues('planningHorizonYear') ?? 0,
    onAccepted: (result) => {
      setPatternSettingsOpen(true);
      focusAdditions(result.change_set?.additions ?? []);
    },
  });
  const previewRecommendation = useLatestPreview<
    RecommendationRequest | BuildingScreenRequest,
    RecommendationPreview
  >({
    scopeKey: JSON.stringify([
      ...projectBasis,
      selectedPatternZoneIds,
      recommendationValues,
    ]),
    execute: (request, signal, publish) =>
      previewWorkspaceRecommendation(projectId, request, signal, publish),
    onAccepted: (result) => {
      setPatternSettingsOpen(true);
      focusAdditions(result.change_set?.additions ?? []);
      onSuggestionAccepted();
    },
  });
  const previewAutomatic = useLatestPreview<
    AutomaticPlacementRequest,
    AutomaticPlacementPreview
  >({
    scopeKey: JSON.stringify(projectBasis),
    execute: (request, signal, publish) =>
      previewWorkspaceAutomatic(projectId, request, signal, publish),
    onAccepted: (result) => {
      focusAdditions(result.change_set?.additions ?? []);
      onSuggestionAccepted();
    },
  });
  const previewBrush = useLatestPreview<BrushPreviewRequest, BrushPreview>({
    scopeKey: JSON.stringify([
      ...projectBasis,
      tool,
      selectedPatternZoneIds,
      brushSettings,
      brushWidth,
      brushOperation,
      brushStrokes,
    ]),
    execute: (request, signal) =>
      previewWorkspaceBrush(projectId, request, signal),
  });
  const previewChanges = useLatestPreview<PlanChangeSetDraft, ChangeSetPreview>(
    {
      scopeKey: JSON.stringify(projectBasis),
      execute: (draft, signal) => previewPlanChanges(projectId, draft, signal),
      onAccepted: () => {
        onChangesAccepted();
      },
    },
  );
  const resetPattern = previewPattern.reset;
  const resetRecommendation = previewRecommendation.reset;
  const resetAutomatic = previewAutomatic.reset;
  const resetBrush = previewBrush.reset;
  const resetChanges = previewChanges.reset;
  const resetPlacementPreviews = useCallback(() => {
    resetPattern();
    resetRecommendation();
    resetAutomatic();
    resetBrush();
    resetChanges();
    setRecommendationOpen(false);
    setBrushStrokes([]);
    setSpeciesAssignmentOpen(false);
  }, [
    resetPattern,
    resetRecommendation,
    resetAutomatic,
    resetBrush,
    resetChanges,
  ]);
  const manualPreview = previewChanges.data;
  const previewDraft = manualPreview
    ? { previewId: manualPreview.id, draft: previewChanges.variables }
    : undefined;
  const invalidatePattern = previewPattern.invalidate;
  const subscribePattern = patternForm.subscribe;
  useEffect(
    () =>
      subscribePattern({
        formState: { values: true },
        callback: invalidatePattern,
      }),
    [subscribePattern, invalidatePattern],
  );
  const invalidateBrush = previewBrush.invalidate;
  const invalidateRecommendation = previewRecommendation.invalidate;
  const subscribeRecommendation = recommendationForm.subscribe;
  useEffect(
    () =>
      subscribeRecommendation({
        formState: { values: true },
        callback: invalidateRecommendation,
      }),
    [subscribeRecommendation, invalidateRecommendation],
  );
  const subscribeBrush = brushForm.subscribe;
  useEffect(
    () =>
      subscribeBrush({
        formState: { values: true },
        callback: invalidateBrush,
      }),
    [subscribeBrush, invalidateBrush],
  );
  const patternPreview =
    previewPattern.data &&
    !previewPattern.isCurrent() &&
    previewPattern.data.change_set
      ? {
          ...previewPattern.data,
          change_set: { ...previewPattern.data.change_set, can_apply: false },
        }
      : previewPattern.data;
  const recommendationPreview = previewRecommendation.data;
  const automaticPreview = previewAutomatic.data;
  const brushPreview = previewBrush.data;
  const changePreview =
    patternPreview?.change_set ??
    recommendationPreview?.change_set ??
    automaticPreview?.change_set ??
    brushPreview?.change_set ??
    manualPreview;
  return {
    rowAxis,
    setRowAxis,
    rowInputMode,
    setRowInputMode,
    rowDrawingPoints,
    setRowDrawingPoints,
    patternForm,
    rowSettings,
    rowAxisSource,
    setRowAxisSource,
    selectedPatternZoneIds,
    setSelectedPatternZoneIds,
    recommendationOpen,
    setRecommendationOpen,
    brushStrokes,
    setBrushStrokes,
    brushWidth,
    setBrushWidth,
    brushOperation,
    setBrushOperation,
    brushForm,
    brushSettings,
    brushDrawing,
    setBrushDrawing,
    speciesAssignmentOpen,
    setSpeciesAssignmentOpen,
    speciesCatalogBrowsing,
    setSpeciesCatalogBrowsing,
    patternSettingsOpen,
    setPatternSettingsOpen,
    singleTreeSpecies,
    setSingleTreeSpecies,
    singleShrubSpecies,
    setSingleShrubSpecies,
    recommendationForm,
    previewPattern,
    previewRecommendation,
    previewAutomatic,
    previewBrush,
    previewChanges,
    manualPreview,
    previewDraft,
    patternPreview,
    recommendationPreview,
    automaticPreview,
    brushPreview,
    changePreview,
    resetPlacementPreviews,
  };
}
