import type { FC } from 'react';
import { Button, FormActions } from '@green/ui';
import {
  Crosshair,
  PenLine,
  MousePointer2,
  ArrowLeftRight,
} from 'lucide-react';
import { sourceLayerLabel } from '@/entities/source-data/model/sourceLabels';
import type { RowAxis } from '@/entities/planting';

export interface RowAxisControlsProps {
  axis?: RowAxis;
  source?: { type: 'dxf' | 'manual'; label: string };
  mode: 'pick' | 'draw' | 'ready';
  points: number;
  length: number;
  loading?: boolean;
  onModeChange?: (mode: 'pick' | 'draw') => void;
  onFinish?: () => void;
  onFit?: () => void;
  onReverse?: () => void;
}

export const RowAxisControls: FC<RowAxisControlsProps> = ({
  axis,
  source,
  mode,
  points,
  length,
  loading,
  onModeChange,
  onFinish,
  onFit,
  onReverse,
}) => (
  <section className="grid gap-3" aria-label="Линия посадок">
    <h3 className="m-0 text-xs font-semibold">Линия</h3>
    <FormActions layout="equal" minItemWidth="10rem">
      <Button
        variant={mode === 'pick' ? 'primary' : 'secondary'}
        icon={MousePointer2}
        aria-pressed={mode === 'pick'}
        disabled={loading}
        onClick={() => onModeChange?.('pick')}
      >
        Выбрать в DXF
      </Button>
      <Button
        variant={mode === 'draw' ? 'primary' : 'secondary'}
        icon={PenLine}
        aria-pressed={mode === 'draw'}
        disabled={loading}
        onClick={() => onModeChange?.('draw')}
      >
        Нарисовать линию
      </Button>
    </FormActions>
    {mode === 'draw' ? (
      <>
        <p className="m-0 text-xs leading-4 text-neutral-600">
          Отметьте точки на карте. Завершите двойным щелчком или кнопкой.
        </p>
        <FormActions layout="equal">
          <Button variant="secondary" disabled={points < 2} onClick={onFinish}>
            Завершить линию
          </Button>
        </FormActions>
      </>
    ) : (
      mode === 'pick' && (
        <p className="m-0 text-xs leading-4 text-neutral-600">
          Наведите на линию чертежа и выберите её щелчком.
        </p>
      )
    )}
    {axis && (
      <>
        <dl className="m-0 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs text-neutral-600 [&_dd]:m-0 [&_dd]:text-right [&_dd]:text-neutral-800">
          <dt>Источник</dt>
          <dd>
            {source?.type === 'manual'
              ? 'Своя линия'
              : sourceLayerLabel(source?.label)}
          </dd>
          <dt>Длина</dt>
          <dd>{length.toFixed(1)} м</dd>
        </dl>
        <FormActions layout="equal">
          <Button
            variant="ghost"
            icon={Crosshair}
            aria-label="Показать линию"
            onClick={onFit}
          >
            Показать
          </Button>
          <Button
            variant="ghost"
            icon={ArrowLeftRight}
            aria-label="Развернуть направление"
            disabled={loading}
            onClick={onReverse}
          >
            Развернуть
          </Button>
        </FormActions>
      </>
    )}
  </section>
);
