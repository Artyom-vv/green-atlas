import { PatternStepActions } from './PatternStepActions';
import type { FC, ReactNode } from 'react';
import type { PatternPreview } from '@green/api-client';
import { Button, FormActions } from '@green/ui';
import type { PatternMode } from '../model/patternForm';

export interface PatternFooterProps {
  mode: PatternMode;
  step: number;
  guided: boolean;
  catalogOpen: boolean;
  preview?: PatternPreview;
  preparation?: PatternPreview;
  loading?: boolean;
  calculating?: boolean;
  canPreview: boolean;
  validSpecies: boolean;
  shortlistLoading?: boolean;
  hasZones: boolean;
  hasAxis: boolean;
  onStep: (step: number) => void;
  onCloseCatalog: () => void;
  onEditPreview: () => void;
  onApply?: () => void;
  onSubmit: () => void;
  onCancel: () => void;
  onCancelCalculation?: () => void;
}

export const PatternFooter: FC<PatternFooterProps> = ({
  mode,
  step,
  guided,
  catalogOpen,
  preview,
  preparation,
  loading,
  calculating,
  canPreview,
  validSpecies,
  shortlistLoading,
  hasZones,
  hasAxis,
  onStep,
  onCloseCatalog,
  onEditPreview,
  onApply,
  onSubmit,
  onCancel,
  onCancelCalculation,
}) => {
  let actions: ReactNode;
  let minItemWidth: string | undefined;
  if (calculating && onCancelCalculation) {
    minItemWidth = '10rem';
    actions = (
      <>
        <Button variant="secondary" onClick={onCancelCalculation}>
          Отменить расчёт
        </Button>
        <Button variant="primary" loading disabled>
          Проверяем
        </Button>
      </>
    );
  } else if (catalogOpen) {
    actions = (
      <Button variant="secondary" onClick={onCloseCatalog}>
        Назад к составу
      </Button>
    );
  } else if (preparation || (preview && !preview.accepted_count && preview.search_domains?.some((domain) => domain.stop_reason !== 'resolution'))) {
    actions = (
      <>
        <Button variant="secondary" disabled={loading} onClick={onEditPreview}>Изменить условия</Button>
        <Button variant="primary" disabled={loading || !canPreview || !validSpecies || shortlistLoading} onClick={onSubmit}>Продолжить проверку</Button>
      </>
    );
  } else if (preview?.change_set) {
    minItemWidth = '10rem';
    actions = (
      <>
        <Button variant="secondary" disabled={loading} onClick={onEditPreview}>
          Изменить
        </Button>
        <Button
          variant="primary"
          loading={loading}
          disabled={!preview.change_set.can_apply || !preview.accepted_count}
          onClick={onApply}
        >
          Добавить {preview.accepted_count}
        </Button>
      </>
    );
  } else if (preview) {
    actions = (
      <Button variant="primary" disabled={loading} onClick={onEditPreview}>
        Изменить условия
      </Button>
    );
  } else if (guided && step < 2) {
    if (step === 1) minItemWidth = '12rem';
    actions = (
      <PatternStepActions
        loading={loading}
        step={step}
        onStep={onStep}
        onCancel={onCancel}
        hasZones={hasZones}
        validSpecies={validSpecies}
        shortlistLoading={shortlistLoading}
      />
    );
  } else {
    if (loading || !hasZones) minItemWidth = '10rem';
    actions = (
      <>
        <Button
          variant="secondary"
          disabled={loading}
          onClick={guided ? () => onStep(1) : onCancel}
        >
          {guided ? 'Назад' : 'Отмена'}
        </Button>
        <Button
          variant="primary"
          loading={loading}
          disabled={!canPreview || !validSpecies || shortlistLoading}
          onClick={onSubmit}
        >
          {!hasZones
            ? 'Выберите участок'
            : mode === 'row' && !hasAxis
              ? 'Выберите линию'
              : 'Проверить места'}
        </Button>
      </>
    );
  }
  return (
    <FormActions layout="equal" minItemWidth={minItemWidth}>
      {actions}
    </FormActions>
  );
};
