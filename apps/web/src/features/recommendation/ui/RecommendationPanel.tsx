import type { FC } from 'react';
import {
  FormProvider,
  useFormContext,
  type UseFormReturn,
} from 'react-hook-form';
import { InlineMessage } from '@green/ui';
import { PlantingZonePicker } from '@/entities/planting-zone';
import { PlacementToolSurface } from '@/shared/ui/TaskSurface';
import { WorkflowSteps } from '@/shared/ui/WorkflowSteps';
import {
  RECOMMENDATION_STEPS,
  type RecommendationFormValues,
} from '../model/recommendationForm';
import { useRecommendationForm } from '../model/useRecommendationForm';
import {
  useRecommendationNavigation,
  type RecommendationNavigationOptions,
} from '../model/useRecommendationNavigation';
import { GoalParameters } from './GoalParameters';
import { RecommendationFooter } from './RecommendationFooter';
export interface RecommendationPanelProps extends RecommendationNavigationOptions {
  form?: UseFormReturn<RecommendationFormValues>;
  loading?: boolean;
  calculating?: boolean;
  onCancelCalculation?: () => void;
  error?: string;
}
const OwnedRecommendationPanel: FC<RecommendationPanelProps> = (props) => {
  const form = useRecommendationForm();
  return (
    <FormProvider {...form}>
      <RecommendationForm {...props} />
    </FormProvider>
  );
};
export const RecommendationPanel: FC<RecommendationPanelProps> = (props) =>
  props.form ? (
    <FormProvider {...props.form}>
      <RecommendationForm {...props} />
    </FormProvider>
  ) : (
    <OwnedRecommendationPanel {...props} />
  );

const RecommendationForm: FC<RecommendationPanelProps> = (props) => {
  const form = useFormContext<RecommendationFormValues>();
  const flow = useRecommendationNavigation(form, props);
  const {
    guided = false,
    loading,
    calculating = false,
    onCancelCalculation,
    error,
  } = props;
  return (
    <PlacementToolSurface
      title="Подобрать по цели"
      showHeader={!guided}
      bodyRef={flow.body}
      steps={
        guided && (
          <WorkflowSteps
            labels={RECOMMENDATION_STEPS}
            current={flow.step}
            label="Шаги подбора"
          />
        )
      }
      footer={
        <RecommendationFooter
          guided={guided}
          step={flow.step}
          loading={loading}
          calculating={calculating}
          canContinue={flow.canContinue}
          onCancelCalculation={onCancelCalculation}
          onBack={flow.back}
          onContinue={flow.next}
        />
      }
    >
      <div hidden={guided && flow.step !== 0}>
        <PlantingZonePicker
          expanded={guided}
          zones={props.zones}
          selectedIds={flow.workZoneIds}
          onChange={flow.onZonesChange}
          disabled={loading}
        />
      </div>
      <div hidden={guided && flow.step !== 1}>
        <GoalParameters disabled={loading} />
      </div>
      {error && <InlineMessage tone="error">{error}</InlineMessage>}
    </PlacementToolSurface>
  );
};
