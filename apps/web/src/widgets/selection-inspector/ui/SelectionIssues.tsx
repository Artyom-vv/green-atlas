import type { FC } from 'react';
import { Disclosure } from '@green/ui';
import { TriangleAlert } from 'lucide-react';
import type { SelectionProblem } from '../model/selectionProblems';

export interface SelectionIssuesProps {
  problems: SelectionProblem[];
}
export const SelectionIssues: FC<SelectionIssuesProps> = ({ problems }) => {
  if (!problems.length) return null;
  const errors = problems.filter(
    (problem) => problem.severity === 'error',
  ).length;
  const warnings = problems.length - errors;
  return (
    <section
      className="grid gap-2 border-0 border-t border-solid border-neutral-200 pt-3"
      data-severity={errors ? 'error' : 'warning'}
      aria-label="Проблемы выбранных объектов"
    >
      <div
        role="status"
        aria-label={`Ошибки: ${errors}. Замечания: ${warnings}.`}
        className="text-warning-strong flex flex-wrap items-center gap-2 text-xs"
      >
        <TriangleAlert size={14} aria-hidden="true" />
        {errors > 0 && <span>Ошибки {errors}</span>}
        {warnings > 0 && <span>Замечания {warnings}</span>}
      </div>
      <Disclosure title={`Что требует внимания (${problems.length})`}>
        <ul className="m-0 grid list-none gap-3 p-0">
          {problems.map((problem) => (
            <li key={problem.key} className="grid gap-1">
              <strong className="text-xs font-medium">{problem.title}</strong>
              <small className="text-xs leading-4 text-neutral-600">
                {problem.description}
              </small>
              {problem.meta && (
                <small className="font-mono text-[11px] text-neutral-600">
                  {problem.meta}
                </small>
              )}
            </li>
          ))}
        </ul>
      </Disclosure>
    </section>
  );
};
