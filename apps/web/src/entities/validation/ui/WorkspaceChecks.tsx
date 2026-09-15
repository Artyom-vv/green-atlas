import type { FC } from 'react';
import type { PlanObject, ValidationIssue } from '@green/api-client';
import { Icon } from '@green/ui';
import { CircleCheck, Sprout } from 'lucide-react';
import { ResultPanel } from '@/shared/ui/results/ResultPanel';
import { groupWorkspaceIssues } from '../model/issueGroups';
import { CheckIssueGroup } from './CheckIssueGroup';

export interface WorkspaceChecksProps {
  disabled?: boolean;
  issues: ValidationIssue[];
  objects?: PlanObject[];
  onLocate: (ids: string[]) => void;
  onAssign: (ids: string[]) => void;
}
export const WorkspaceChecks: FC<WorkspaceChecksProps> = ({
  disabled,
  issues,
  objects = [],
  onLocate,
  onAssign,
}) => {
  const groups = groupWorkspaceIssues(issues, objects);
  return (
    <ResultPanel
      title="Проверка посадок"
      label="Проверка проекта"
      count={`Замечаний: ${issues.length}`}
    >
      {!groups.length ? (
        <div
          className="mx-auto my-2 flex max-w-xl items-start gap-3 p-3 text-neutral-600"
          role="status"
        >
          <Icon
            icon={objects.length ? <CircleCheck /> : <Sprout />}
            size={22}
          />
          <div>
            <strong className="font-semibold text-neutral-800">
              {objects.length ? 'Посадки проверены' : 'Пока нечего проверять'}
            </strong>
            <p className="mt-1 mb-0">
              {objects.length
                ? 'По загруженным данным нарушений не обнаружено.'
                : 'Добавьте посадки на рабочий участок — здесь появятся результаты проверки отступов и пересечений.'}
            </p>
          </div>
        </div>
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
