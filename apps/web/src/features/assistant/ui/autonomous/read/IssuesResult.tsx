import { readExcerpt } from '@/features/assistant/model/autonomous/presentation';
import { AssistantCard } from '@/features/assistant/ui/shared/AssistantCard';
import { Text } from '@green/ui';
import type { FC } from 'react';
import { ReadEvidence } from '../ReadEvidence';
import { IssueList } from './IssueList';
import type { ReadResultsProps } from './ReadResults.types';
export interface IssuesResultProps extends Pick<
  ReadResultsProps,
  'issues' | 'project'
> {}
export const IssuesResult: FC<IssuesResultProps> = ({ issues, project }) => (
  <>
    {!!issues && (
      <AssistantCard aria-label="Сохранённые замечания" tone="neutral">
        <span className="text-xs font-medium text-neutral-600">
          Результат проверки
          {issues.planVersion !== undefined
            ? `, план ${issues.planVersion}`
            : ''}
        </span>
        <Text as="h3" variant="heading" className="m-0 wrap-anywhere">
          {issues.total === 0
            ? 'В сохранённых результатах замечаний нет'
            : `Сохранённых замечаний: ${issues.total}`}
        </Text>
        <ReadEvidence read={issues.read} project={project} />
        {issues.items.length > 0 && <IssueList items={issues.items} />}
        {readExcerpt(issues.total, issues.items.length, issues.read) ? (
          <Text as="p" variant="body" className="m-0 wrap-anywhere">
            {readExcerpt(issues.total, issues.items.length, issues.read)}
          </Text>
        ) : issues.total > issues.items.length ? (
          <Text as="p" variant="body" className="m-0 wrap-anywhere">
            Показано замечаний: {issues.items.length} из {issues.total}.
          </Text>
        ) : null}
        {!!(
          project?.plan &&
          issues.planVersion !== undefined &&
          project.plan.version !== issues.planVersion
        ) && (
          <Text as="p" variant="body" className="m-0 wrap-anywhere">
            Эти результаты относятся к предыдущей версии плана.
          </Text>
        )}
        <Text className="text-xs text-neutral-600" as="p" variant="body">
          {issues.read.caveat ??
            'Это сохранённые результаты для запрошенной области. Новая проверка не выполнялась; отсутствие записей не исключает других ограничений.'}
        </Text>
      </AssistantCard>
    )}
  </>
);
