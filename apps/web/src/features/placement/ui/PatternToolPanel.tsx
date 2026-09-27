import type { FC } from 'react';
import { FormProvider, type UseFormReturn } from 'react-hook-form';
import type {
  PatternPreview,
  PlacementMaskPreset,
  PlantingZoneAssignment,
  SpeciesRevision,
  SpeciesShortlistItem,
} from '@green/api-client';
import type { RowAxis } from '@/entities/planting';
import type { GrowthHorizon } from '@/entities/planting-forecast';
import { PlacementToolSurface } from '@/shared/ui/TaskSurface';
import { WorkflowSteps } from '@/shared/ui/WorkflowSteps';
import type {
  PatternDraft,
  PatternFormValues,
  PatternMode,
} from '../model/patternForm';
import { usePatternForm } from '../model/usePatternForm';
import { usePatternWorkflow } from '../model/usePatternWorkflow';
import { PatternFooter } from './PatternFooter';
import { PatternFormContent } from './PatternFormContent';
import type { CandidateInspection } from '../model/useCandidateInspection';
export interface PatternToolPanelProps {
  mode: PatternMode;
  form?: UseFormReturn<PatternFormValues>;
  guided?: boolean;
  zones: PlantingZoneAssignment[];
  species: SpeciesRevision[];
  shortlist?: SpeciesShortlistItem[];
  shortlistLoading?: boolean;
  placementMasks?: PlacementMaskPreset[];
  axis?: RowAxis;
  axisSource?: { type: 'dxf' | 'manual'; label: string };
  axisMode?: 'pick' | 'draw' | 'ready';
  axisDrawingPoints?: number;
  onAxisModeChange?: (mode: 'pick' | 'draw') => void;
  onReverseAxis?: () => void;
  onFitAxis?: () => void;
  onFinishAxis?: () => void;
  selectedZoneIds?: string[];
  drawingZone?: boolean;
  loading?: boolean;
  calculating?: boolean;
  onCancelCalculation?: () => void;
  error?: string;
  preview?: PatternPreview;
  preparation?: PatternPreview;
  inspection?: CandidateInspection;
  growthHorizon?: GrowthHorizon;
  onGrowthHorizon?: (year: GrowthHorizon) => void;
  onSelectedZoneIdsChange?: (ids: string[]) => void;
  onDrawZone?: () => void;
  onPreview: (draft: PatternDraft) => void;
  onApply?: () => void;
  onResetPreview?: () => void;
  onCancel: () => void;
}

/** A standalone host owns a form; the workspace supplies its longer-lived form. */
const OwnedPatternTool: FC<PatternToolPanelProps> = (props) => {
  const form = usePatternForm();
  return (
    <FormProvider {...form}>
      <PatternForm {...props} />
    </FormProvider>
  );
};

export const PatternToolPanel: FC<PatternToolPanelProps> = (props) =>
  props.form ? (
    <FormProvider {...props.form}>
      <PatternForm {...props} />
    </FormProvider>
  ) : (
    <OwnedPatternTool key={props.mode} {...props} />
  );

const PatternForm: FC<PatternToolPanelProps> = (props) => {
  const workflow = usePatternWorkflow({
    ...props,
    hasPreview: Boolean(props.preview || props.preparation),
  });
  const {
    mode,
    guided = false,
    preview,
    drawingZone = false,
    loading,
    calculating = false,
    shortlistLoading,
    onCancel,
    onResetPreview,
  } = props;
  return (
    <PlacementToolSurface
      title={mode === 'row' ? 'Ряд посадок' : 'Разместить посадки'}
      showHeader={!guided}
      steps={
        guided && (
          <WorkflowSteps
            labels={['Участки', 'Состав', 'Размещение', 'Проверка']}
            current={preview || props.preparation || calculating ? 3 : workflow.step}
            label="Шаги размещения"
          />
        )
      }
      bodyRef={workflow.bodyRef}
      bodyTabIndex={-1}
      bodyClassName={
        workflow.catalog
          ? 'flex h-full min-h-0 flex-col overflow-hidden'
          : undefined
      }
      footer={
        !drawingZone && (
          <PatternFooter
            mode={mode}
            step={workflow.step}
            guided={guided}
            catalogOpen={Boolean(workflow.catalog)}
            preview={preview}
            preparation={props.preparation}
            loading={loading}
            calculating={calculating}
            canPreview={workflow.canPreview}
            validSpecies={workflow.validSpecies}
            shortlistLoading={shortlistLoading}
            hasZones={(props.selectedZoneIds?.length ?? 0) > 0}
            hasAxis={Boolean(props.axis)}
            onStep={workflow.setStep}
            onCloseCatalog={() => workflow.setCatalog(undefined)}
            onEditPreview={onResetPreview ? workflow.editPreview : onCancel}
            onApply={props.onApply}
            onSubmit={workflow.submit}
            onCancel={onCancel}
            onCancelCalculation={props.onCancelCalculation}
          />
        )
      }
    >
      <PatternFormContent options={props} workflow={workflow} />
    </PlacementToolSurface>
  );
};
