import type { FC } from 'react';
import type { ChangeReviewSummary } from '../model/changeReviewSummary';

export interface ChangeReviewImpactProps {
  impact: Pick<ChangeReviewSummary, 'additions' | 'updates' | 'deletions'>;
}

export const ChangeReviewImpact: FC<ChangeReviewImpactProps> = ({
  impact: { additions, updates, deletions },
}) => (
  <dl className="m-0 grid grid-cols-[minmax(0,1fr)_auto] gap-x-6 gap-y-2 [&_dd]:m-0 [&_dd]:tabular-nums [&_dt]:text-neutral-600">
    {additions > 0 && (
      <>
        <dt>Добавить</dt>
        <dd>{additions}</dd>
      </>
    )}
    {updates > 0 && (
      <>
        <dt>Изменить</dt>
        <dd>{updates}</dd>
      </>
    )}
    {deletions > 0 && (
      <>
        <dt>Удалить</dt>
        <dd>{deletions}</dd>
      </>
    )}
  </dl>
);
