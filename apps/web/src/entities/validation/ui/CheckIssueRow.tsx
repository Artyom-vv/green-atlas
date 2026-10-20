import type { FC } from 'react';
import type { ValidationIssue } from '@green/api-client';
import { IconButton } from '@green/ui';
import { Crosshair } from 'lucide-react';
import {
  RESULTS_ACTION_RAIL,
  RESULTS_CONTENT_INSET,
} from '@/shared/ui/results/resultsLayout';
import { issueObjectIds } from '../model/issueGroups';

export interface CheckIssueRowProps {
  issue: ValidationIssue;
  hasMetrics?: boolean;
  onLocate: (ids: string[]) => void;
}
const numberFormat = new Intl.NumberFormat('ru-RU');
export const CheckIssueRow: FC<CheckIssueRowProps> = ({
  issue,
  hasMetrics,
  onLocate,
}) => {
  const ids = issueObjectIds(issue);
  const metric = (value: number | null | undefined) =>
    value == null
      ? 'Нет данных'
      : `${numberFormat.format(value)}${issue.unit ? ` ${issue.unit}` : ''}`;
  const columns = hasMetrics
    ? 'grid grid-cols-[minmax(0,1fr)_5rem_5rem] items-center gap-3'
    : 'grid grid-cols-1';
  return (
    <div
      role="row"
      className="grid grid-cols-[minmax(0,1fr)_auto] items-center border-t border-neutral-200"
    >
      <div className={`min-w-0 ${RESULTS_CONTENT_INSET} py-2`}>
        <div className={columns}>
          <span role="cell" className="min-w-0 wrap-anywhere">
            {issue.description}
          </span>
          {hasMetrics && (
            <>
              <span role="cell" className="text-right font-mono tabular-nums">
                {metric(issue.actual)}
              </span>
              <span role="cell" className="text-right font-mono tabular-nums">
                {metric(issue.required)}
              </span>
            </>
          )}
        </div>
      </div>
      <div className={RESULTS_ACTION_RAIL}>
        <span role="cell" className="col-start-2">
          {ids.length > 0 && (
            <IconButton
              variant="ghost"
              controlSize="compact"
              icon={Crosshair}
              label={`Показать ${ids.length === 1 ? 'посадку' : 'связанные посадки'}: ${issue.title}`}
              onClick={() => onLocate(ids)}
            />
          )}
        </span>
        <span aria-hidden="true" className="col-start-3 h-8 w-8" />
      </div>
    </div>
  );
};
