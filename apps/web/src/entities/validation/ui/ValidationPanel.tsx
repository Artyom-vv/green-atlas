import type { FC } from 'react';
import type { ValidationIssue } from '@green/api-client';
import { AlertTriangle, LocateFixed } from 'lucide-react';
import {
  Button,
  ControlProvider,
  EmptyState,
  Icon,
  ListRow,
  StatusIndicator,
} from '@green/ui';
import { groupIssuesByRule } from '../model/issueGroups';

export interface ValidationPanelProps {
  issues: ValidationIssue[];
  onLocate: (objectId?: string) => void;
}
export const ValidationPanel: FC<ValidationPanelProps> = ({
  issues,
  onLocate,
}) => {
  const errors = issues.filter((item) => item.severity === 'error').length;
  const warnings = issues.filter((item) => item.severity === 'warning').length;
  return (
    <ControlProvider size="compact">
      <section className="grid min-w-0 gap-4 text-xs">
        <div className="flex flex-wrap gap-3">
          <StatusIndicator
            tone={errors ? 'error' : 'success'}
            label="Ошибки"
            value={errors}
          />
          <StatusIndicator
            tone={warnings ? 'warning' : 'neutral'}
            label="Риски"
            value={warnings}
          />
        </div>
        {!issues.length ? (
          <EmptyState title="Нарушений нет" />
        ) : (
          <div className="grid gap-4">
            {groupIssuesByRule(issues).map(([basis, items]) => (
              <section key={basis} aria-label={`Основание ${basis}`}>
                <header className="flex items-start justify-between gap-3 border-0 border-b border-solid border-neutral-200 pb-2">
                  <span className="flex min-w-0 flex-col gap-1">
                    <strong className="font-mono text-xs">{basis}</strong>
                    <small className="text-xs text-neutral-600">
                      {items[0].title}
                    </small>
                  </span>
                  <span className="text-neutral-600 tabular-nums">
                    {items.length}
                  </span>
                </header>
                {items.map((issue) => (
                  <ListRow
                    key={issue.id}
                    leading={
                      <Icon
                        icon={<AlertTriangle />}
                        className={
                          issue.severity === 'error'
                            ? 'text-error'
                            : 'text-warning'
                        }
                      />
                    }
                    title={
                      issue.object_id ? 'Посадка требует действия' : issue.title
                    }
                    description={`${issue.description}${issue.suggested_action ? ` Действие: ${issue.suggested_action}` : ''}`}
                    meta={
                      issue.actual != null
                        ? `${issue.actual} / ${issue.required} ${issue.unit ?? ''}`
                        : undefined
                    }
                    actions={
                      issue.object_id ? (
                        <Button
                          variant="ghost"
                          startIcon={<LocateFixed />}
                          onClick={() => onLocate(issue.object_id ?? undefined)}
                        >
                          Показать
                        </Button>
                      ) : undefined
                    }
                  />
                ))}
              </section>
            ))}
          </div>
        )}
      </section>
    </ControlProvider>
  );
};
