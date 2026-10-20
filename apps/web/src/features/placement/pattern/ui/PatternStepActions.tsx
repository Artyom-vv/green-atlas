import type { FC } from 'react';
import { Button } from '@green/ui';
import type { PatternFooterProps } from './PatternFooter';
interface PatternStepActionsProps extends Pick<
  PatternFooterProps,
  | 'loading'
  | 'step'
  | 'onStep'
  | 'onCancel'
  | 'hasZones'
  | 'validSpecies'
  | 'shortlistLoading'
> {}
export const PatternStepActions: FC<PatternStepActionsProps> = ({
  loading,
  step,
  onStep,
  onCancel,
  hasZones,
  validSpecies,
  shortlistLoading,
}) => (
  <>
    <Button
      variant="secondary"
      disabled={loading}
      onClick={step ? () => onStep(step - 1) : onCancel}
    >
      {step ? 'Назад' : 'Отмена'}
    </Button>
    <Button
      variant="primary"
      disabled={
        loading || (step === 0 ? !hasZones : !validSpecies || shortlistLoading)
      }
      onClick={() => onStep(step + 1)}
    >
      {step === 0 ? 'Выбрать состав' : 'Настроить размещение'}
    </Button>
  </>
);
