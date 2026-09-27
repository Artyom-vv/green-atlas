import type { ChangeSetPreview } from '@green/api-client';
import { Button, Dialog, FormActions } from '@green/ui';
import { ChangeReviewProblems } from './ChangeReviewProblems';
import { ChangeReviewDetails } from './ChangeReviewDetails';
import { ChangeReviewImpact } from './ChangeReviewImpact';
import type { FC } from 'react';
import { changeReviewSummary } from '../model/changeReviewSummary';

export interface ChangeSetReviewPanelProps {
  preview: ChangeSetPreview;
  open?: boolean;
  applying?: boolean;
  error?: string;
  note?: string;
  rejectedReasons?: string[];
  unverifiedData?: string[];
  onApply: () => void;
  onCancel: () => void;
  onInspect?: () => void;
}

export const ChangeSetReviewPanel: FC<ChangeSetReviewPanelProps> = ({
  preview,
  open = true,
  applying,
  error,
  note,
  rejectedReasons = [],
  unverifiedData = [],
  onApply,
  onCancel,
  onInspect,
}) => {
  const {
    additions,
    updates,
    deletions,
    blocked,
    uncertain,
    problems,
    rejected,
  } = changeReviewSummary(preview, rejectedReasons);
  const close = () => {
    if (!applying) (onInspect ?? onCancel)();
  };
  return (
    <Dialog
      open={open}
      title={
        blocked.length
          ? 'Изменение недоступно'
          : !preview.can_apply
            ? 'Нужна проверка'
            : 'Применить изменения?'
      }
      onClose={close}
      footer={
        <FormActions layout="equal" minItemWidth="12rem">
          <Button variant="secondary" disabled={applying} onClick={onCancel}>
            Отменить изменение
          </Button>
          {onInspect && (
            <Button variant="secondary" disabled={applying} onClick={onInspect}>
              Посмотреть на карте
            </Button>
          )}
          {preview.can_apply && (
            <Button variant="primary" loading={applying} onClick={onApply}>
              Применить
            </Button>
          )}
        </FormActions>
      }
    >
      <div className="grid gap-4 text-sm leading-normal">
        <p className="m-0 font-medium">{preview.label}</p>
        <ChangeReviewImpact impact={{ additions, updates, deletions }} />
        <ChangeReviewProblems
          problems={problems}
          affectedCount={blocked.length + uncertain.length}
        />
        {!preview.can_apply && problems.length === 0 && (
          <p>
            Вариант не прошёл проверку. Вернитесь к параметрам или отмените
            изменение.
          </p>
        )}
        {Boolean(note) && <p>{note}</p>}
        <ChangeReviewDetails
          unverifiedData={unverifiedData}
          rejected={rejected}
        />
        {Boolean(error) && (
          <p className="text-error m-0" role="alert">
            {error}
          </p>
        )}
        {preview.can_apply && (
          <p className="m-0 text-xs text-neutral-600">
            До подтверждения сохранённый план не меняется.
          </p>
        )}
      </div>
    </Dialog>
  );
};
