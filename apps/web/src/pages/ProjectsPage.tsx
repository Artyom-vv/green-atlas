/** Экран библиотеки: закреплённое управление, выбор проектов и безопасное удаление. */
import { ProjectDeletionDialog } from '@/features/projects/ui/ProjectDeletionDialog';
import { ProjectListEmpty } from '@/features/projects/ui/ProjectListEmpty';
import type { FC } from 'react';
import { FilePlus2, ListChecks, Trash2, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Button, IconButton, InlineMessage, Progress, Text } from '@green/ui';
import { useProjects, ProjectTable } from '@/features/projects';
import { ProjectWorkProvider } from '@/features/project-navigation/model/ProjectWorkProvider';
import { countLabel } from '@/shared/format/countLabel';
import { ComponentSettings } from '@/features/component-settings/ComponentSettings';

const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : undefined;

export const ProjectsPage: FC = () => (
  <ProjectWorkProvider>
    <ProjectsSession />
  </ProjectWorkProvider>
);

const ProjectsSession: FC = () => {
  const navigate = useNavigate();
  const {
    query,
    deletion,
    recovery,
    writeOutcome,
    readRecovery,
    recoveryError,
    deleteCandidates,
    progress,
    chooseDeletion,
    confirmDeletion,
    selectedProjects,
    toggleSelection,
    toggleAll,
    clearSelection,
  } = useProjects();
  const projects = query.data ?? [];
  const queryError = errorMessage(query.error);
  const navigationBlocked = deletion.isPending || writeOutcome === 'unknown';
  const actionsBlocked = navigationBlocked || readRecovery || query.isError;
  const selectedIds = new Set(selectedProjects.map((project) => project.id));
  const newProject = () => navigate('/projects/new');

  return (
    <div className="flex h-dvh min-h-0 flex-col overflow-hidden bg-white">
      <header className="z-10 flex min-h-13 shrink-0 flex-wrap items-center justify-between gap-3 border-b border-neutral-200 bg-white px-6 py-2">
        <Text as="strong" variant="label" className="text-sm">
          Проекты озеленения
        </Text>
        <div className="flex items-center gap-2">
          <ComponentSettings />
          <Button
            variant="primary"
            startIcon={<FilePlus2 />}
            disabled={navigationBlocked}
            onClick={newProject}
          >
            Новый проект
          </Button>
        </div>
      </header>
      <main className="mx-auto flex min-h-0 w-full max-w-280 flex-1 flex-col gap-5 px-6 py-8">
        <div className="flex shrink-0 items-center gap-3">
          <Text as="h1" variant="pageHeading" className="m-0">
            Проекты
          </Text>
          {query.isSuccess && projects.length > 0 && (
            <Text variant="caption">{projects.length}</Text>
          )}
        </div>
        {query.isPending && <Progress label="Загружаем проекты" />}
        {deletion.isPending && deleteCandidates.length === 0 && (
          <Progress label="Обновляем библиотеку" />
        )}
        {queryError && (
          <InlineMessage tone="error" title="Не удалось загрузить проекты">
            <div className="flex min-w-0 flex-wrap items-center gap-3">
              <span className="min-w-0 flex-1 wrap-anywhere">{queryError}</span>
              <Button
                variant="secondary"
                controlSize="compact"
                loading={query.isFetching}
                onClick={() => void query.refetch()}
              >
                Повторить
              </Button>
            </div>
          </InlineMessage>
        )}
        {query.isSuccess && !queryError && projects.length === 0 && (
          <ProjectListEmpty onCreate={newProject} />
        )}
        {readRecovery &&
          writeOutcome === 'confirmed' &&
          deleteCandidates.length === 0 && (
            <InlineMessage title="Проекты удалены, обновите библиотеку">
              <Button
                variant="secondary"
                loading={recovery.isPending}
                onClick={() => recovery.mutate()}
              >
                Обновить библиотеку
              </Button>
              {Boolean(recoveryError) && (
                <Text role="alert">{errorMessage(recoveryError)}</Text>
              )}
            </InlineMessage>
          )}
        {projects.length > 0 && (
          <>
            <div
              aria-label="Действия с проектами"
              className="flex min-h-10 shrink-0 flex-wrap items-center gap-2"
            >
              <Button
                variant="ghost"
                startIcon={<ListChecks />}
                disabled={actionsBlocked}
                onClick={toggleAll}
              >
                {selectedProjects.length === projects.length
                  ? 'Снять выбор'
                  : 'Выбрать все'}
              </Button>
              {selectedProjects.length > 0 && (
                <div className="motion-safe:animate-surface-enter ml-auto flex flex-wrap items-center gap-2">
                  <Text
                    role="status"
                    aria-live="polite"
                    variant="label"
                    className="mr-1 text-blue-600"
                  >
                    Выбрано {selectedProjects.length} из {projects.length}
                  </Text>
                  <IconButton
                    icon={<X />}
                    label="Очистить выбор"
                    variant="ghost"
                    disabled={actionsBlocked}
                    onClick={clearSelection}
                  />
                  <Button
                    variant="danger"
                    startIcon={<Trash2 />}
                    disabled={actionsBlocked}
                    onClick={() => chooseDeletion(selectedProjects)}
                  >
                    Удалить выбранные
                  </Button>
                </div>
              )}
            </div>
            <ProjectTable
              projects={projects}
              selectedIds={selectedIds}
              removedIds={progress?.completedIds ?? []}
              busy={actionsBlocked}
              onSelect={toggleSelection}
              onDelete={(project) => chooseDeletion([project])}
            />
          </>
        )}
        {writeOutcome === 'confirmed' &&
          !readRecovery &&
          progress &&
          deleteCandidates.length === 0 && (
            <Text role="status" aria-live="polite" variant="caption">
              Удалено{' '}
              {countLabel(
                progress.completedIds.length,
                'проект',
                'проекта',
                'проектов',
              )}
            </Text>
          )}
      </main>
      <ProjectDeletionDialog
        projects={deleteCandidates}
        progress={progress}
        deleting={deletion.isPending}
        unknown={writeOutcome === 'unknown'}
        recoveryRequired={readRecovery}
        recovering={recovery.isPending}
        error={errorMessage(recoveryError) ?? errorMessage(deletion.error)}
        onRecover={() => recovery.mutate()}
        onClose={() => chooseDeletion()}
        onDelete={confirmDeletion}
      />
    </div>
  );
};
