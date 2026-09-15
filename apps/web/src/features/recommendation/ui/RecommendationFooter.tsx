import type { FC } from 'react';
import { Button, FormActions } from '@green/ui';

export interface RecommendationFooterProps {
  guided: boolean;
  step: number;
  textEntry: boolean;
  buildingScreen: boolean;
  interpreting: boolean;
  loading?: boolean;
  calculating: boolean;
  canContinue: boolean;
  onCancelCalculation?: () => void;
  onBack: () => void;
  onContinue: () => void;
}
export const RecommendationFooter: FC<RecommendationFooterProps> = ({
  guided,
  step,
  textEntry,
  buildingScreen,
  interpreting,
  loading,
  calculating,
  canContinue,
  onCancelCalculation,
  onBack,
  onContinue,
}) => (
  <FormActions layout="equal" minItemWidth="10rem">
    {calculating && onCancelCalculation ? (
      <>
        <Button variant="secondary" onClick={onCancelCalculation}>
          Отменить расчёт
        </Button>
        <Button variant="primary" loading disabled>
          Проверяем
        </Button>
      </>
    ) : (
      <>
        <Button variant="secondary" disabled={loading} onClick={onBack}>
          {guided && step
            ? interpreting
              ? 'Отменить разбор'
              : 'Назад'
            : 'Отмена'}
        </Button>
        <Button
          variant="primary"
          loading={loading || interpreting}
          disabled={!canContinue || interpreting}
          onClick={onContinue}
        >
          {guided && !step
            ? 'Выбрать задачу'
            : textEntry
              ? interpreting
                ? 'Разбираем задачу'
                : 'Разобрать задачу'
              : buildingScreen
                ? 'Показать на карте'
                : guided
                  ? 'Проверить места'
                  : 'Показать'}
        </Button>
      </>
    )}
  </FormActions>
);
