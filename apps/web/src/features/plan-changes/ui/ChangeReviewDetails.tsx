import type { FC } from 'react';
import { EditorDisclosure } from '@/shared/ui/inspector/EditorPanel';
import type { ChangeReviewSummary } from '../model/changeReviewSummary';

export interface ChangeReviewDetailsProps {
  unverifiedData: string[];
  rejected: ChangeReviewSummary['rejected'];
}

export const ChangeReviewDetails: FC<ChangeReviewDetailsProps> = ({
  unverifiedData,
  rejected,
}) => (
  <>
    {unverifiedData.length > 0 && (
      <EditorDisclosure title="Ограничения исходных данных">
        <ul className="m-0 grid list-none gap-2 p-0">
          {unverifiedData.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </EditorDisclosure>
    )}
    {rejected.length > 0 && (
      <EditorDisclosure title="Исключено при поиске">
        <ul className="m-0 grid list-none gap-2 p-0">
          {rejected.map(([reason, count]) => (
            <li key={reason}>
              {count}: {reason}
            </li>
          ))}
        </ul>
      </EditorDisclosure>
    )}
  </>
);
