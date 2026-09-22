import { Button } from '@green/ui';

export function SourceReviewNotice({
  onReview,
  incompleteGeometry = false,
  displayWarning,
  calculationPending = false,
}: {
  onReview: () => void;
  incompleteGeometry?: boolean;
  displayWarning?: string;
  calculationPending?: boolean;
}) {
  return (
    <Button
      variant="ghost"
      controlSize="compact"
      onClick={onReview}
      title={displayWarning ?? 'Редактирование доступно. Сведения о полноте данных — в исходных данных.'}
    >
      {calculationPending ? 'Расчёт ограничений не выполнен — подготовить карту' : displayWarning ? 'Показана доступная геометрия' : incompleteGeometry
        ? 'Исходные данные: есть замечания'
        : 'Сверить исходные данные'}
    </Button>
  );
}
