import type { WorkspaceRecommendationToolProps } from './WorkspaceRecommendationTool.props';
/** Рабочая область: выбор сценария подбора и видимое состояние его выполнения */
import { recommendationToolBindings } from './recommendationToolBindings';
import type { FC } from 'react';
import { useState } from 'react';
import { AutomaticPlacementPanel } from './AutomaticPlacementPanel';
import { Button } from '@green/ui';
import { PlacementToolSurface } from '@/shared/ui/TaskSurface';
import { SearchDomainProgress } from '@/features/placement/inspection/ui/SearchDomainProgress';
import {
  RecommendationPanel,
  RecommendationReviewPanel,
} from '@/features/recommendation';
export const WorkspaceRecommendationTool: FC<
  WorkspaceRecommendationToolProps
> = (props) => {
  const [detailed, setDetailed] = useState(false);
  const {
    applyChanges,
    growthHorizon,
    previewChanges,
    previewRecommendation,
    project,
    recommendationForm,
    recommendationPreview,
    selectPatternZones,
    selectedPatternZoneIds,
    setGrowthHorizon,
    speciesNames,
  } = props;
  if (!detailed || props.automaticPreview) {
    return (
      <AutomaticPlacementPanel
        {...props}
        onDetailed={() => setDetailed(true)}
      />
    );
  }
  if (previewRecommendation.isPending) {
    return (
      <PlacementToolSurface
        title="Подбираем посадки"
        showHeader={false}
        footer={
          <Button
            variant="secondary"
            onClick={() => previewRecommendation.cancel()}
          >
            Отменить
          </Button>
        }
      >
        <SearchDomainProgress
          preview={previewRecommendation.progress}
          running
        />
      </PlacementToolSurface>
    );
  }
  return (
    <>
      <div
        className="flex min-h-0 min-w-0 flex-1 flex-col"
        hidden={Boolean(recommendationPreview)}
      >
        <RecommendationPanel
          {...recommendationToolBindings(props)}
          form={recommendationForm}
          calculating={previewRecommendation.isPending}

          guided
          selectedZoneIds={selectedPatternZoneIds}
          onSelectedZoneIdsChange={selectPatternZones}
          zones={project.planting_zones ?? []}
          loading={previewRecommendation.isPending}
        />
      </div>
      {recommendationPreview && (
        <RecommendationReviewPanel
          requestedCount={previewRecommendation.variables?.max_sites}
          speciesNames={speciesNames}
          proposal={recommendationPreview}
          growthHorizon={growthHorizon}
          onGrowthHorizon={setGrowthHorizon}
          applying={applyChanges.isPending}
          onApply={() => {
            if (recommendationPreview.change_set?.can_apply)
              applyChanges.mutate(recommendationPreview.change_set);
          }}
          onCancel={() => {
            previewRecommendation.reset();
            previewChanges.reset();
          }}
        />
      )}
    </>
  );
};
