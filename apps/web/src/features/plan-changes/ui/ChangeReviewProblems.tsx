import type { FC } from 'react';
import { EditorDisclosure } from '@/shared/ui/inspector/EditorPanel';
import type { ChangeReviewSummary } from '../model/changeReviewSummary';

export interface ChangeReviewProblemsProps {
  problems: ChangeReviewSummary['problems'];
  affectedCount: number;
}

export const ChangeReviewProblems: FC<ChangeReviewProblemsProps> = ({
  problems,
  affectedCount,
}) => (
  <>
    {problems.length > 0 && (
      <section aria-label="Причины ограничения">
        <p className="mt-0 mb-3 font-medium">
          Не проходят проверку: {affectedCount}
        </p>
        <ul className="m-0 grid list-none gap-2 p-0">
          {problems.slice(0, 2).map((item) => (
            <li key={item.reason}>
              {item.reason}
              {item.count > 1 && (
                <small className="mt-1 block text-xs text-neutral-600">
                  Позиций: {item.count}
                </small>
              )}
            </li>
          ))}
        </ul>
        {(problems.length > 2 ||
          problems.some((item) => item.details.length > 1)) && (
          <EditorDisclosure title="Все причины">
            <ul className="m-0 grid list-none gap-2 p-0">
              {problems.map((item) => (
                <li key={item.reason}>
                  <strong className="mb-1 block font-medium">
                    Позиций: {item.count}
                  </strong>
                  {item.details.map((reason) => (
                    <p key={reason}>{reason}</p>
                  ))}
                </li>
              ))}
            </ul>
          </EditorDisclosure>
        )}
      </section>
    )}
  </>
);
