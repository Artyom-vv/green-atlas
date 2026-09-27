import { Button } from '@green/ui';
import { TriangleAlert } from 'lucide-react';

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
  const label = calculationPending
    ? 'Расчёт ограничений не выполнен — подготовить карту'
    : displayWarning
      ? 'Показана доступная геометрия'
      : incompleteGeometry
        ? 'Исходные данные: есть замечания'
        : 'Сверить исходные данные';
  return (
    <Button
      icon={TriangleAlert}
      variant="ghost"
      controlSize="compact"
      onClick={onReview}
      aria-label={label}
      className="shrink-0 text-amber-700"
      title={displayWarning ?? `${label}. Открыть исходные данные проекта.`}
    >
      <span className="hidden sm:inline">
        {calculationPending ? 'Подготовить карту' : displayWarning || incompleteGeometry ? 'Замечания' : 'Исходные данные'}
      </span>
    </Button>
  );
}
