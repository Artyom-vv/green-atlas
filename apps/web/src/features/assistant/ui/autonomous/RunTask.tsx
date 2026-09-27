import type { FC } from 'react';
import { RestoreTask } from './task/RestoreTask';
import type { RunTaskProps } from './task/RunTask.types';
import { SubmittedTask } from './task/SubmittedTask';
import { TaskProgress } from './task/TaskProgress';
import { TaskSummary } from './task/TaskSummary';
import { TaskWelcome } from './task/TaskWelcome';
export type { RunTaskProps } from './task/RunTask.types';
export const RunTask: FC<RunTaskProps> = (props) => (
  <>
    <RestoreTask
      failedRestore={props.failedRestore}
      restoring={props.restoring}
      decisionDisabled={props.decisionDisabled}
      retryRestore={props.retryRestore}
    />
    <TaskWelcome
      run={props.run}
      restoring={props.restoring}
      failedRestore={props.failedRestore}
      creating={props.creating}
      fillDraft={props.fillDraft}
      decisionDisabled={props.decisionDisabled}
    />
    <TaskSummary
      run={props.run}
      active={props.active}
      result={props.result}
      existing={props.existing}
      issues={props.issues}
      shortlist={props.shortlist}
      status={props.status}
      acceptedSelectionLabel={props.acceptedSelectionLabel}
    />
    <SubmittedTask
      creating={props.creating}
      draft={props.draft}
      submitted={props.submitted}
    />
    <TaskProgress
      active={props.active}
      run={props.run}
      lifecycle={props.lifecycle}
      busy={props.busy}
      decisionDisabled={props.decisionDisabled}
      continueRun={props.continueRun}
      stopping={props.stopping}
      canCancel={props.canCancel}
      restoring={props.restoring}
      cancel={props.cancel}
    />
  </>
);
