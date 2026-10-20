/** Шкала временного сценария роста над картой; результаты принадлежат проверке */
import { GrowthHorizonControl } from '@/entities/planting-forecast';
import { canvasSurfaceClassName } from '@/shared/ui/CanvasControls';

interface Props {
  year: number;
  onYear: (year: number) => void;
}

export function GrowthScenarioPanel({ year, onYear }: Props) {
  return (
    <GrowthHorizonControl
      className={`${canvasSurfaceClassName} w-[320px] max-w-full`}
      appearance="viewport"
      value={year}
      showMetrics={false}
      onChange={(next) => onYear(next ?? 0)}
    />
  );
}
