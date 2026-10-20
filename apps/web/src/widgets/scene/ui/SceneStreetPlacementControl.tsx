import { Button } from '@green/ui';
import { PersonStanding } from 'lucide-react';
import type { StreetCameraPlacementState } from '../model/streetCameraPlacement';
import { SceneNotice } from './SceneStatus';

export function SceneStreetPlacementControl({
  state,
  onConfirm,
  onRestart,
  onCancel,
}: {
  state: StreetCameraPlacementState;
  onConfirm: () => void;
  onRestart: () => void;
  onCancel: () => void;
}) {
  return (
    <SceneNotice
      title={
        state.step === 'position'
          ? '1. Поставьте камеру'
          : state.step === 'direction'
            ? '2. Укажите направление'
            : 'Уличный ракурс готов'
      }
      icon={PersonStanding}
      action={
        <>
          <Button disabled={state.step !== 'ready'} onClick={onConfirm}>
            Применить ракурс
          </Button>
          {state.position && (
            <Button variant="secondary" onClick={onRestart}>
              Другая точка
            </Button>
          )}
          <Button variant="secondary" onClick={onCancel}>
            Отменить
          </Button>
        </>
      }
    >
      <p className="m-0">
        {state.step === 'position'
          ? 'Нажмите в месте съёмки на дороге или тротуаре.'
          : state.step === 'direction'
            ? 'Синяя точка — камера. Нажмите там, куда она будет смотреть.'
            : 'Синяя точка — камера, оранжевая — направление. Нажмите в другом месте, чтобы изменить направление.'}
      </p>
      <p className="mt-2 mb-0">
        Высота 1,7 м. Горизонт ровный. Shift — направление с шагом 45° по осям
        чертежа.
      </p>
      {state.snapped && (
        <p className="mt-1 mb-0 font-medium">
          Направление привязано к осям чертежа
        </p>
      )}
      <p className="mt-1 mb-0">Enter — применить. Escape — отменить.</p>
    </SceneNotice>
  );
}
