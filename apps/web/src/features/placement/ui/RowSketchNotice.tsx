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
      'Отступы длиннее линии'
    ) : (
      <div className="grid gap-1">
        <strong>Эскиз: {total} позиций</strong>
        <dl
          className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 [&_dd]:m-0"
          aria-label="Легенда эскиза"
        >
          <dt>Синие точки</dt>
          <dd>Не проверены</dd>
          <dt>Красные точки</dt>
          <dd>За границей участков</dd>
        </dl>
      </div>
    )}
  </InlineMessage>
);
