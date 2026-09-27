import type { FC } from 'react';
import { Button, Disclosure, Icon } from '@green/ui';
import {
  CircleAlert,
  Crosshair,
  LockKeyhole,
  TriangleAlert,
} from 'lucide-react';
import type { WorkspaceIssueGroup } from '../model/issueGroups';
import { CheckIssueRow } from './CheckIssueRow';

export interface CheckIssueGroupProps {
  group: WorkspaceIssueGroup;
  disabled?: boolean;
  onLocate: (ids: string[]) => void;
  onAssign: (ids: string[]) => void;
}

export const CheckIssueGroup: FC<CheckIssueGroupProps> = ({
  group: { items, ids, title, missing, hasError, locked },
  disabled,
  onLocate,
  onAssign,
}) => (
  <div
    className="border-0 border-b border-solid border-neutral-200 last:border-b-0"
    role="group"
    aria-label={title}
  >
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 py-2">
      <div className="grid min-w-0 gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`rounded-control inline-flex shrink-0 items-center gap-1 px-2 py-1 text-xs ${hasError ? 'bg-error-soft text-error' : 'bg-warning-soft text-warning-strong'}`}
          >
            <Icon
              icon={hasError ? <CircleAlert /> : <TriangleAlert />}
              size={13}
            />
            {hasError ? 'Ошибка' : 'Предупреждение'}
          </span>
          <strong className="font-medium wrap-anywhere">{title}</strong>
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-neutral-600 tabular-nums">
          <span>Замечаний: {items.length}</span>
          {ids.length > 0 && <span>Посадок: {ids.length}</span>}
          {missing && <span>Данные о посадках</span>}
        </div>
        {missing && locked && (
          <span className="inline-flex items-center gap-1 text-neutral-600">
            <Icon icon={<LockKeyhole />} size={12} />
            Закреплены. Снимите закрепление перед назначением.
          </span>
        )}
      </div>
      <div className="ml-auto flex shrink-0 flex-wrap items-center gap-2">
        {ids.length > 0 && (
          <Button
            variant="ghost"
            startIcon={<Crosshair />}
            aria-label={`Показать на карте: ${title}, посадок: ${ids.length}`}
            onClick={() => onLocate(ids)}
          >
            На карте
          </Button>
        )}
        {missing && ids.length > 0 && !locked && (
          <Button
            variant="secondary"
            disabled={disabled}
            onClick={() => onAssign(ids)}
          >
            Назначить виды
          </Button>
        )}
      </div>
    </div>
    {!missing && (
      <Disclosure
        title="Подробности проверки"
        variant="plain"
        contentClassName="gap-0 py-0"
      >
        {items.map((issue, index) => (
          <CheckIssueRow
            key={issue.id ?? index}
            issue={issue}
            onLocate={onLocate}
          />
        ))}
      </Disclosure>
    )}
  </div>
);
