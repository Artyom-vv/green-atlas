import { Button, Dialog, FormActions, Text } from '@green/ui';
import type { FC } from 'react';

export interface FinishPlacementDialogProps {
  open: boolean;
  onClose: () => void;
}

export const FinishPlacementDialog: FC<FinishPlacementDialogProps> = ({
  open,
  onClose,
}) => (
  <Dialog
    open={open}
    title="Сначала завершите расстановку"
    onClose={onClose}
    footer={
      <FormActions layout="equal">
        <Button variant="primary" onClick={onClose}>
          Вернуться к расстановке
        </Button>
      </FormActions>
    }
  >
    <Text as="p">
      В 3D показан сохранённый план. Примените или отмените текущий
      предпросмотр, чтобы перейти к нему.
    </Text>
  </Dialog>
);
