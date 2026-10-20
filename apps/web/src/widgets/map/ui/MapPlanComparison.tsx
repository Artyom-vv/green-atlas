import type { PlanComparisonMode } from '@/entities/editor/model/planComparison';
import { Button, IconButton, SegmentedControl } from '@green/ui';
import { EyeOff, Sprout } from 'lucide-react';

interface Props {
  mode: PlanComparisonMode;
  canEnter: boolean;
  hasDraft: boolean;
  onChange: (mode: PlanComparisonMode) => void;
  embedded?: boolean;
}

const modes = [
  { value: 'before', label: 'До' },
  { value: 'after', label: 'После' },
] as const;

export function MapPlanComparison({
  mode,
  canEnter,
  hasDraft,
  onChange,
  embedded = false,
}: Props) {
  if (embedded)
    return (
      <>
        <IconButton
          icon={mode === 'editing' ? Sprout : EyeOff}
          variant="ghost"
          controlSize="compact"
          label="Показать без посадок плана"
          className="aria-pressed:bg-blue-100 aria-pressed:text-blue-700 aria-pressed:hover:bg-blue-200"
          aria-pressed={mode !== 'editing'}
          disabled={mode === 'editing' && !canEnter}
          onClick={() => onChange(mode === 'editing' ? 'before' : 'editing')}
        />
        {mode !== 'editing' && (
          <span role="status" className="sr-only">
            {mode === 'before' ? 'Исходная территория' : 'Применённые посадки'}
            {hasDraft ? '. Черновик не включён' : ''}
          </span>
        )}
        {mode !== 'editing' && hasDraft && (
          <p className="rounded-control absolute top-full right-0 m-0 mt-2 border border-solid border-neutral-300 bg-white px-3 py-2 text-xs text-neutral-600">
            Черновик не включён в сравнение
          </p>
        )}
      </>
    );
  if (mode === 'editing')
    return (
      <Button
        variant="secondary"
        controlSize="compact"
        disabled={!canEnter}
        onClick={() => onChange('after')}
      >
        Сравнить до/после
      </Button>
    );
  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      <SegmentedControl
        label="Территория до и после озеленения"
        controlSize="compact"
        value={mode}
        options={modes.map((option) => ({ ...option, disabled: !canEnter }))}
        onChange={onChange}
      />
      <Button
        variant="secondary"
        controlSize="compact"
        onClick={() => onChange('editing')}
      >
        К редактированию
      </Button>
      <span
        className="rounded-control basis-full bg-white/90 px-2 py-1 text-right text-xs text-neutral-600"
        role="status"
      >
        {mode === 'before' ? 'Исходная территория' : 'Применённые посадки'}
        {hasDraft ? '. Черновик не включён' : ''}
      </span>
    </div>
  );
}
