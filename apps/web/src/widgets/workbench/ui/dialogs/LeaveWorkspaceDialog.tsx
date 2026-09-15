import { Button, Dialog, FormActions } from '@green/ui';
import type { FC } from 'react';

export interface LeaveWorkspaceDialogProps {
  open: boolean;
  pending: boolean;
  onStay: () => void;
  onLeave: () => void;
}

export const LeaveWorkspaceDialog: FC<LeaveWorkspaceDialogProps> = ({
  open,
  pending,
  onStay,
  onLeave,
}) => (
  <Dialog
    open={open}
    title={pending ? 'Дождитесь завершения операции' : 'Уйти без применения?'}
    onClose={onStay}
    footer={
      <FormActions layout="equal" minItemWidth="12rem">
        <Button variant="secondary" onClick={onStay}>
          Остаться
        </Button>
        {!pending && (
          <Button variant="primary" onClick={onLeave}>
            Уйти без применения
          </Button>
        )}
      </FormActions>
    }
  >
    <p>
      {pending
        ? 'Операция ещё выполняется. Дождитесь результата перед выходом из плана.'
        : 'Предпросмотр и незавершённое рисование не сохранятся. Уже применённые посадки останутся в плане.'}
    </p>
  </Dialog>
);
