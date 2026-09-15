import { useState, type FC } from 'react';
import type { RecommendationPreview } from '@green/api-client';
import { Button, Dialog, FormActions, InlineMessage } from '@green/ui';
import {
  GrowthHorizonControl,
  type GrowthHorizon,
} from '@/entities/planting-forecast';
import { plantingCount } from '@/shared/format/countLabel';
import { PlacementToolSurface } from '@/shared/ui/TaskSurface';
import { WorkflowSteps } from '@/shared/ui/WorkflowSteps';
import { RECOMMENDATION_STEPS } from '../model/recommendationForm';
import { recommendationComposition } from '../model/recommendationSummary';
import { RecommendationEvidence } from './RecommendationEvidence';

export interface RecommendationReviewPanelProps {
  proposal: RecommendationPreview;
  requestedCount?: number | null;
  speciesNames?: Map<string, string>;
  applying?: boolean;
  growthHorizon?: GrowthHorizon;
  onGrowthHorizon?: (value: GrowthHorizon) => void;
  onApply: () => void;
  onCancel: () => void;
}
export const RecommendationReviewPanel: FC<RecommendationReviewPanelProps> = ({
  proposal,
  requestedCount,
  speciesNames,
  applying,
  growthHorizon,
  onGrowthHorizon,
  onApply,
  onCancel,
}) => {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const count = proposal.change_set?.additions?.length ?? 0;
  const composition = recommendationComposition(proposal, speciesNames);
  const footer = (
    <FormActions layout="equal" minItemWidth="10rem">
      <Button variant="secondary" disabled={applying} onClick={onCancel}>
        Изменить условия
      </Button>
      <Button
        variant="primary"
        loading={applying}
        disabled={!proposal.change_set?.can_apply || !count}
        onClick={onApply}
      >
        Добавить {count}
      </Button>
    </FormActions>
  );
  return (
    <PlacementToolSurface
      title="Предложение готово"
      showHeader={false}
      footer={footer}
      steps={
        <WorkflowSteps
          labels={RECOMMENDATION_STEPS}
          current={2}
          label="Шаги подбора"
        />
      }
    >
      <h3 className="m-0 text-base font-semibold">{plantingCount(count)}</h3>
      {composition && (
        <p
          className="m-0 text-sm text-neutral-700"
          aria-label="Состав предложения"
        >
          {composition}
        </p>
      )}
      {count > 0 && onGrowthHorizon && (
        <GrowthHorizonControl
          value={growthHorizon}
          forecasts={proposal.change_set?.additions ?? []}
          onChange={onGrowthHorizon}
          showMetrics={false}
        />
      )}
      {proposal.arrangement === 'building_screen' && count > 0 && (
        <p className="m-0 text-xs leading-5 text-neutral-600">
          Скрытие фасадов не оценивалось.
        </p>
      )}
      {!!proposal.data_gaps?.length && (
        <p className="m-0 text-xs leading-5 text-neutral-600">
          В исходных данных есть пробелы.
        </p>
      )}
      <Button
        variant="secondary"
        className="justify-self-start"
        onClick={() => setDetailsOpen(true)}
      >
        Что учтено
      </Button>
      <Dialog
        open={detailsOpen}
        title="Что учтено в предложении"
        onClose={() => setDetailsOpen(false)}
      >
        <RecommendationEvidence
          proposal={proposal}
          requestedCount={requestedCount}
        />
      </Dialog>
      {!count && (
        <InlineMessage tone="warning">
          Здесь не найдено свободных мест для этой схемы. Выберите другие
          участки или измените задачу.
        </InlineMessage>
      )}
    </PlacementToolSurface>
  );
};
