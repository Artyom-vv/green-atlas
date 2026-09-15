import { isProjectConflict } from '@/entities/project/model/projectConflict';
import { ProjectConflictNotice } from '@/entities/project/ui/ProjectConflictNotice';
import { errorMessage } from '@/shared/errors/errorMessage';
import { Button, Dialog, FormActions, InlineMessage } from '@green/ui';
import { useLayoutEffect, useRef, useState, type FC } from 'react';

export interface OperationErrorDialogProps {
  open: boolean;
  error: unknown;
  onClose: () => void;
  onReload: () => Promise<void>;
}

export const OperationErrorDialog: FC<OperationErrorDialogProps> = ({
  open,
  error,
  onClose,
  onReload,
}) => {
  const [reloading, setReloading] = useState(false);
  const [recoveryError, setRecoveryError] = useState<unknown>();
  const activeRequest = useRef<object | undefined>(undefined);
  useLayoutEffect(() => {
    activeRequest.current = undefined;
    setReloading(false);
    setRecoveryError(undefined);
    return () => {
      activeRequest.current = undefined;
    };
  }, [open, error]);
  const close = () => {
    activeRequest.current = undefined;
    setReloading(false);
    setRecoveryError(undefined);
    onClose();
  };
  const reload = async () => {
    if (activeRequest.current || !open) return;
    const attempt = {};
    activeRequest.current = attempt;
    setReloading(true);
    setRecoveryError(undefined);
    try {
      await onReload();
      if (activeRequest.current !== attempt) return;
      onClose();
    } catch (failure) {
      if (activeRequest.current === attempt) setRecoveryError(failure);
    } finally {
      if (activeRequest.current === attempt) {
        activeRequest.current = undefined;
        setReloading(false);
      }
    }
  };
  return (
    <Dialog
      open={open}
      title={
        isProjectConflict(error)
          ? 'План изменился'
          : 'Не удалось выполнить действие'
      }
      onClose={close}
      footer={
        <FormActions layout="equal">
          <Button variant="primary" onClick={close}>
            Вернуться к работе
          </Button>
        </FormActions>
      }
    >
      {isProjectConflict(error) ? (
        <ProjectConflictNotice
          error={error}
          onReload={() => void reload()}
          reloading={reloading}
        />
      ) : (
        <p>{errorMessage(error)}</p>
      )}
      {Boolean(recoveryError) && (
        <InlineMessage tone="error">
          {errorMessage(recoveryError)}
        </InlineMessage>
      )}
    </Dialog>
  );
};
