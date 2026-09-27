import type { FC } from 'react';
import { AssistantCard } from '../shared/AssistantCard';
import { PlacementComposition } from './placement/PlacementComposition';
import { PlacementExplanation } from './placement/PlacementExplanation';
import { PlacementRemedies } from './placement/PlacementRemedies';
import type { PlacementResultProps } from './placement/PlacementResult.types';
import { PlacementSummary } from './placement/PlacementSummary';
export type { PlacementResultProps } from './placement/PlacementResult.types';
export const PlacementResult: FC<PlacementResultProps> = (props) => {
  const { result, reviewStatus } = props;
  if (!result) return null;
  return (
    <AssistantCard
      aria-label="Результат размещения"
      tone={
        reviewStatus
          ? 'muted'
          : result.status === 'exact'
            ? 'neutral'
            : 'warning'
      }
    >
      <PlacementSummary
        reviewStatus={props.reviewStatus}
        result={result}
        reviewNotice={props.reviewNotice}
      />
      <PlacementComposition
        scopeLabels={props.scopeLabels}
        zones={props.zones}
        speciesIds={props.speciesIds}
        speciesNames={props.speciesNames}
        catalog={props.catalog}
        arrangementLabel={props.arrangementLabel}
        kinds={props.kinds}
      />
      <PlacementExplanation
        result={result}
        incomplete={props.incomplete}
        reviewStatus={props.reviewStatus}
        run={props.run}
        committed={props.committed}
        approval={props.approval}
        recoveryPending={props.recoveryPending}
        outcomeUnknown={props.outcomeUnknown}
      />
      <PlacementRemedies
        incomplete={props.incomplete}
        result={result}
        inputDisabled={props.inputDisabled}
        fillDraft={props.fillDraft}
        answering={props.answering}
      />
    </AssistantCard>
  );
};
