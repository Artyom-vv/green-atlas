/** Горизонт остаётся черновиком до команды проверки мест */
import { Button, InlineMessage } from '@green/ui';
import { GrowthHorizonControl } from '@/entities/planting-forecast';
import { errorMessage } from '@/shared/errors/errorMessage';

export interface PlanningHorizonFieldProps {
  year: number;
  disabled?: boolean;
  error?: unknown;
  onChange: (year: number) => void;
  onRecover?: () => Promise<void>;
}

export function PlanningHorizonField({
  year,
  disabled,
  error,
  onChange,
  onRecover,
}: PlanningHorizonFieldProps) {
  return (
    <section className="grid gap-2" aria-label="Горизонт расчёта">
      <GrowthHorizonControl
        value={year}
        label="Горизонт расчёта"
        showMetrics={false}
        disabled={disabled}
        onChange={(next) => onChange(next ?? year)}
      />
      {error != null && (
        <InlineMessage tone="error">{errorMessage(error)}</InlineMessage>
      )}
      {onRecover && (
        <Button variant="secondary" onClick={() => void onRecover()}>
          Обновить проект
        </Button>
      )}
    </section>
  );
}
