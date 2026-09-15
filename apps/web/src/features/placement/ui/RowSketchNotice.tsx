import type { FC } from 'react';
import { InlineMessage } from '@green/ui';
interface RowSketchNoticeProps {
  invalidOffsets: boolean;
  total: number;
}
export const RowSketchNotice: FC<RowSketchNoticeProps> = ({
  invalidOffsets,
  total,
}) => (
  <InlineMessage tone={invalidOffsets ? 'error' : 'info'}>
    {invalidOffsets ? (
      'Отступы длиннее линии. Уменьшите их.'
    ) : (
      <div className="grid gap-1">
        <strong>Эскиз: {total} позиций</strong>
        <span>
          Синие точки ещё не проверены. Красные — за выбранными участками.
        </span>
      </div>
    )}
  </InlineMessage>
);
