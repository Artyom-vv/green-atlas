import type { WorkspaceRecommendationToolProps } from './WorkspaceRecommendationTool.props';
import { recommendationToolBindings } from './recommendationToolBindings';
import type { FC } from 'react';
import {
  RecommendationPanel,
  RecommendationReviewPanel,
} from '@/features/recommendation';
import { errorMessage as message } from '@/shared/errors/errorMessage';
export const WorkspaceRecommendationTool: FC<
  WorkspaceRecommendationToolProps
> = (props) => {
  const {
    applyChanges,
    buildingTargets,
    growthHorizon,
    previewChanges,
    previewRecommendation,
    project,
    recommendationForm,
    recommendationOpen,
    recommendationPreview,
    selectPatternZones,
    selectedPatternZoneIds,
    setBuildingScreenActive,
    setGrowthHorizon,
    speciesNames,
  } = props;
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
          active={recommendationOpen}
          onScreenMode={setBuildingScreenActive}
          screenTargets={buildingTargets.data}
          screenLoading={buildingTargets.isFetching}
          screenError={
            buildingTargets.error ? message(buildingTargets.error) : undefined
          }
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
