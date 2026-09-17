import { Button, InlineMessage } from '@green/ui';

export function SourceReviewNotice({
  onReview,
  incompleteGeometry = false,
}: {
  onReview: () => void;
  incompleteGeometry?: boolean;
}) {
  return (
    <InlineMessage tone="warning">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span>
          Редактирование доступно. Посадки пока не проверены по ограничениям
          исходных данных.
          {incompleteGeometry &&
            ' Часть объектов сохранена в DXF, но ещё не представлена расчётной геометрией.'}
        </span>
        <Button variant="secondary" onClick={onReview}>
          Проверить слои
        </Button>
      </div>
    </InlineMessage>
  );
}
