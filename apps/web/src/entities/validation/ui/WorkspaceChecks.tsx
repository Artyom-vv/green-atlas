import type { FC } from 'react';
import type { PlanObject, ValidationIssue } from '@green/api-client';
import { Button } from '@green/ui';
import { growthHorizonLabel } from '@/entities/planting-forecast';
import { ResultPanel } from '@/shared/ui/results/ResultPanel';
import { RESULTS_CONTENT_INSET } from '@/shared/ui/results/resultsLayout';
import { groupWorkspaceIssues } from '../model/issueGroups';
import { CheckIssueGroup } from './CheckIssueGroup';

export interface WorkspaceChecksProps {
  disabled?: boolean;
  issues: ValidationIssue[];
  objects?: PlanObject[];
  onLocate: (ids: string[]) => void;
  onAssign: (ids: string[]) => void;
  embedded?: boolean;
  forecast?: {
    year?: number;
    pending: boolean;
    error?: string;
    retry: () => void;
  };
}
export const WorkspaceChecks: FC<WorkspaceChecksProps> = ({
  disabled,
  issues,
  objects = [],
  onLocate,
  onAssign,
  embedded,
  forecast,
}) => {
  const groups = groupWorkspaceIssues(issues, objects);
  const forecastYear = forecast?.year;
  const scenario = forecastYear !== undefined;
  return (
    <ResultPanel
      title="Проверка посадок"
      label={
        scenario
          ? `Прогноз роста: ${growthHorizonLabel(forecastYear)}`
          : 'Проверка проекта'
      }
      count={
        forecast?.pending || forecast?.error
          ? undefined
          : `Причин: ${groups.length}`
      }
      embedded={embedded}
    >
      {forecast?.pending || forecast?.error ? (
        <div
          className={`${RESULTS_CONTENT_INSET} flex min-h-12 items-center gap-3 py-4`}
        >
          <span role="status" className="text-neutral-600">
            {forecast.error
              ? scenario
                ? 'Не удалось проверить прогноз'
                : 'Не удалось проверить посадки'
              : scenario
                ? 'Проверяем прогноз'
                : 'Проверяем посадки'}
          </span>
          {forecast.error && (
            <Button variant="ghost" onClick={forecast.retry}>
              Повторить
            </Button>
          )}
        </div>
      ) : !groups.length ? (
        <p
          className={`${RESULTS_CONTENT_INSET} py-4 text-neutral-600`}
          role="status"
        >
          {objects.length ? 'Нет адресных замечаний' : 'Пока нечего проверять'}
        </p>
      ) : (
        groups.map((group) => (
          <CheckIssueGroup
            key={group.key}
            group={group}
            disabled={disabled}
            onLocate={onLocate}
            onAssign={onAssign}
          />
        ))
      )}
    </ResultPanel>
  );
};
