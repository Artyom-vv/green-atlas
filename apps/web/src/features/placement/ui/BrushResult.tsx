import type { FC } from 'react';
import type { BrushPreview, BrushStroke } from '@green/api-client';
import { Button, Disclosure, InlineMessage } from '@green/ui';

export interface BrushResultProps {
  strokes: BrushStroke[];
  preview?: BrushPreview;
  loading?: boolean;
  applying: boolean;
  onClear: () => void;
}

export const BrushResult: FC<BrushResultProps> = ({
  strokes,
  preview,
  loading,
  applying,
  onClear,
}) => {
  const additions = strokes.filter((stroke) => stroke.mode === 'add').length;
  return (
    <section
      className="grid gap-2 border-0 border-t border-solid border-neutral-200 pt-3"
      aria-label="Предпросмотр кисти"
    >
      <h3 className="m-0 text-sm font-semibold">Предпросмотр</h3>
      <div role="status" className="text-xs leading-4">
        {loading
          ? 'Проверяем размещение…'
          : preview
            ? `К добавлению: ${preview.added_count}`
            : 'Раскладка ещё не проверена'}
      </div>
      {!!preview?.removed_count && (
        <p className="m-0 text-xs leading-4">
          К удалению: {preview.removed_count}
        </p>
      )}
      {!!preview?.skipped.length && (
        <InlineMessage tone="info">
          Исключено проверкой: {preview.skipped.length}
        </InlineMessage>
      )}
      <Disclosure title={`Мазки: ${strokes.length}`}>
        <p className="m-0 text-xs leading-4">
          добавить {additions}, убрать {strokes.length - additions}
        </p>
        <Button variant="ghost" disabled={applying} onClick={onClear}>
          Очистить мазки
        </Button>
      </Disclosure>
    </section>
  );
};
