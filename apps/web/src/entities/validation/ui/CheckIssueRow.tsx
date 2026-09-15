import type { FC } from 'react';
import type { ValidationIssue } from '@green/api-client';
import { Button } from '@green/ui';
import { Crosshair } from 'lucide-react';
import { issueObjectIds } from '../model/issueGroups';

export interface CheckIssueRowProps {
  issue: ValidationIssue;
  onLocate: (ids: string[]) => void;
}

export const CheckIssueRow: FC<CheckIssueRowProps> = ({ issue, onLocate }) => {
  const ids = issueObjectIds(issue);
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-0 border-t border-solid border-neutral-200 py-2 pl-4">
      <div>
        <p className="m-0">{issue.description}</p>
        {issue.actual != null && issue.required != null && (
          <p className="mt-1 mb-0 text-neutral-600">
            Фактически: {issue.actual} {issue.unit}. Требуется: {issue.required}{' '}
            {issue.unit}.
          </p>
        )}
      </div>
      {ids.length > 0 && (
        <Button
          variant="ghost"
          startIcon={<Crosshair />}
          aria-label={`Показать ${ids.length === 1 ? 'посадку' : 'связанные посадки'}: ${issue.title}`}
          onClick={() => onLocate(ids)}
        >
          {ids.length === 1 ? 'Показать посадку' : 'Показать посадки'}
        </Button>
      )}
    </div>
  );
};
