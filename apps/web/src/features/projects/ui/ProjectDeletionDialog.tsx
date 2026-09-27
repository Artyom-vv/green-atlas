import type { FC } from 'react';
import type { ProjectSummary } from '@green/api-client';
import { Button, Dialog, FormActions, Text } from '@green/ui';
import { Trash2 } from 'lucide-react';
interface ProjectDeletionDialogProps {
  project?: ProjectSummary;
  deleting: boolean;
  onClose: () => void;
  onDelete: () => void;
}
export const ProjectDeletionDialog: FC<ProjectDeletionDialogProps> = ({
  project,
  deleting,
  onClose,
  onDelete,
}) => (
  <Dialog
    open={Boolean(project)}
    title="Удалить проект навсегда?"
    onClose={onClose}
    footer={
      <FormActions layout="equal" minItemWidth="12rem">
        <Button variant="secondary" onClick={onClose} disabled={deleting}>
          Отмена
        </Button>
        <Button
          variant="danger"
          startIcon={<Trash2 />}
          loading={deleting}
          onClick={onDelete}
        >
          Удалить
        </Button>
      </FormActions>
    }
  >
    <Text as="p">
      Проект «{project?.name}», исходный DXF, планы и результаты проверки будут
      удалены без возможности восстановления.
    </Text>
  </Dialog>
);
