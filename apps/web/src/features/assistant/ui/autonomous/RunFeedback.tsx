import type { FC } from 'react';
import { CompletedFeedback } from './feedback/CompletedFeedback';
import { FailedFeedback } from './feedback/FailedFeedback';
import { MapFeedback } from './feedback/MapFeedback';
import { RecoveryFeedback } from './feedback/RecoveryFeedback';
import { RetryFeedback } from './feedback/RetryFeedback';
import type { RunFeedbackProps } from './feedback/RunFeedback.types';
export type { RunFeedbackProps } from './feedback/RunFeedback.types';
export const RunFeedback: FC<RunFeedbackProps> = (props) => (
  <>
    <RetryFeedback
      stale={props.stale}
      retryBlocked={props.retryBlocked}
      busy={props.busy}
      decisionDisabled={props.decisionDisabled}
      retry={props.retry}
      existingUnverified={props.existingUnverified}
      zoneBlocked={props.zoneBlocked}
    />
    <CompletedFeedback
      run={props.run}
      committed={props.committed}
      shownZone={props.shownZone}
      zoneCommitted={props.zoneCommitted}
    />
    <MapFeedback
      mapControl={props.mapControl}
      decisionDisabled={props.decisionDisabled}
    />
    <FailedFeedback
      run={props.run}
      failureRemedy={props.failureRemedy}
      retryBlocked={props.retryBlocked}
      busy={props.busy}
      decisionDisabled={props.decisionDisabled}
      retry={props.retry}
    />
    <RecoveryFeedback
      recoveryPending={props.recoveryPending}
      outcomeUnknown={props.outcomeUnknown}
      busy={props.busy}
      decisionDisabled={props.decisionDisabled}
      refreshOutcome={props.refreshOutcome}
      canCancel={props.canCancel}
      active={props.active}
      approval={props.approval}
      stopping={props.stopping}
      cancel={props.cancel}
      status={props.status}
      error={props.error}
    />
  </>
);
