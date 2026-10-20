/** Подтверждение удаления: выбранные проекты, ход записи и проверка потерянного ответа. */
import type { FC } from 'react';
import type { ProjectSummary } from '@green/api-client';
import { Button, Dialog, FormActions, Progress, Text } from '@green/ui';
import { Trash2 } from 'lucide-react';
import { countLabel } from '@/shared/format/countLabel';
import type { ProjectDeletionProgress } from '../model/useProjectDeletion';

interface ProjectDeletionDialogProps {
  projects: ProjectSummary[];
  progress?: ProjectDeletionProgress;
  deleting: boolean;
  recoveryRequired: boolean;
  unknown: boolean;
  recovering: boolean;
  error?: string;
  onRecover: () => void;
  onClose: () => void;
  onDelete: () => void;
}

export const ProjectDeletionDialog: FC<ProjectDeletionDialogProps> = ({
  projects,
  progress,
  deleting,
  recoveryRequired,
  unknown,
  recovering,
  error,
  onRecover,
  onClose,
  onDelete,
}) => {
  const completed = progress?.completedIds.length ?? 0;
  const title = recoveryRequired
    ? 'Проверить результат удаления'
    : projects.length === 1
      ? 'Удалить проект?'
      : `Удалить ${countLabel(projects.length, 'проект', 'проекта', 'проектов')}?`;
  return (
    <Dialog
      open={projects.length > 0}
      title={title}
      onClose={onClose}
      closeDisabled={deleting || recoveryRequired}
      footer={
        <FormActions layout="equal" minItemWidth="12rem">
          <Button
            variant="secondary"
            onClick={onClose}
            disabled={deleting || recoveryRequired}
          >
            Отмена
          </Button>
          <Button
            startIcon={recoveryRequired ? undefined : <Trash2 />}
            loading={deleting || recovering}
            variant={recoveryRequired ? 'primary' : 'danger'}
            onClick={recoveryRequired ? onRecover : onDelete}
          >
            {recoveryRequired ? 'Проверить результат' : 'Удалить'}
          </Button>
        </FormActions>
      }
    >
      <div className="grid gap-4">
        {progress && (deleting || recoveryRequired) ? (
          <div role="status" aria-live="polite" className="grid gap-2">
            <Progress
              label={
                deleting
                  ? `Удаляем ${progress.currentName}`
                  : `Удалено ${completed} из ${progress.total}`
              }
              value={(completed / progress.total) * 100}
              valueLabel={`${completed} из ${progress.total}`}
            />
            {unknown && (
              <Text variant="caption">
                Ответ потерян — проверьте библиотеку
              </Text>
            )}
          </div>
        ) : (
          <>
            <Text>
              Сохранённый исходник и план будут удалены без возможности
              восстановления
            </Text>
            <ul className="rounded-card m-0 max-h-48 list-none overflow-y-auto bg-neutral-100 p-3">
              {projects.map((project) => (
                <li key={project.id} className="not-last:mb-2">
                  <Text variant="label" className="wrap-anywhere">
                    {project.name}
                  </Text>
                </li>
              ))}
            </ul>
          </>
        )}
        {error && (
          <Text role="alert" className="text-error">
            {error}
          </Text>
        )}
      </div>
    </Dialog>
  );
};
