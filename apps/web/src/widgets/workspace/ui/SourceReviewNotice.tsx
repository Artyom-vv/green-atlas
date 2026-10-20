import { Button } from '@green/ui';
import { TriangleAlert } from 'lucide-react';

export function SourceReviewNotice({
  onReview,
  calculationPending = false,
  preparationIssue,
}: {
  onReview: () => void;
  calculationPending?: boolean;
  preparationIssue?: string;
}) {
  const needsPreparation = Boolean(preparationIssue) || calculationPending;
  if (!needsPreparation) return null;
  return (
    <Button
      icon={TriangleAlert}
      variant="ghost"
      controlSize="compact"
      onClick={onReview}
      aria-label="Подготовить расчёт"
      className="shrink-0 text-amber-700"
      title={preparationIssue ?? 'Расчёт ограничений ещё не выполнен'}
    >
      <span className="hidden sm:inline">Подготовить расчёт</span>
    </Button>
  );
}
