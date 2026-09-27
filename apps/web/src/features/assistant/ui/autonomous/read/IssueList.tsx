import { issueMeasurement } from '@/features/assistant/model/autonomous/presentation';
import { Text } from '@green/ui';
import type { FC } from 'react';
import type { ReadResultsProps } from './ReadResults.types';
interface IssueListProps {
  items: NonNullable<ReadResultsProps['issues']>['items'];
}
export const IssueList: FC<IssueListProps> = ({ items }) => (
  <ul className="m-0 grid list-none gap-2 p-0">
    {items.map((issue, index) => (
      <li
        key={typeof issue.id === 'string' ? issue.id : index}
        className="grid min-w-0 gap-1 border-t border-neutral-200 pt-2"
      >
        <span
          className={
            issue.severity === 'error'
              ? 'text-error text-xs'
              : 'text-warning-strong text-xs'
          }
        >
          {issue.severity === 'error'
            ? 'Ошибка'
            : issue.severity === 'warning'
              ? 'Предупреждение'
              : 'Замечание'}
        </span>
        <Text as="strong" variant="label" className="m-0 wrap-anywhere">
          {String(issue.title)}
        </Text>
        {typeof issue.description === 'string' && (
          <Text as="p" variant="body" className="m-0 wrap-anywhere">
            {issue.description}
          </Text>
        )}
        {!!issueMeasurement(issue) && (
          <Text as="small" variant="caption" className="m-0 wrap-anywhere">
            {issueMeasurement(issue)}
          </Text>
        )}
      </li>
    ))}
  </ul>
);
