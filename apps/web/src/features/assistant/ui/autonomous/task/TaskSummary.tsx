import { Disclosure, Text } from '@green/ui';
import type { FC } from 'react';
import type { RunTaskProps } from './RunTask.types';
export interface TaskSummaryProps extends Pick<
  RunTaskProps,
  | 'run'
  | 'active'
  | 'result'
  | 'existing'
  | 'issues'
  | 'shortlist'
  | 'status'
  | 'acceptedSelectionLabel'
> {}
export const TaskSummary: FC<TaskSummaryProps> = ({
  run,
  active,
  result,
  existing,
  issues,
  shortlist,
  status,
  acceptedSelectionLabel,
}) => (
  <>
    {!!run && (
      <Disclosure
        variant="plain"
        defaultOpen={
          active ||
          (!result &&
            !existing &&
            !issues &&
            !shortlist &&
            status !== 'waiting_approval' &&
            status !== 'finished')
        }
        title={
          <>
            <span>Задание</span>
          </>
        }
      >
        <Text as="p" variant="body" className="m-0 wrap-anywhere">
          {String(run.state.intent.raw_text ?? '')}
        </Text>
      </Disclosure>
    )}
    {!!acceptedSelectionLabel && (
      <Text
        aria-label="Область задания"
        className="text-xs text-neutral-600"
        as="p"
        variant="body"
      >
        Область задания — выделение: {acceptedSelectionLabel}.
      </Text>
    )}
  </>
);
