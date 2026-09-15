import { ProjectDeletionDialog } from '@/features/projects/ui/ProjectDeletionDialog';
import { ProjectListEmpty } from '@/features/projects/ui/ProjectListEmpty';
import type { FC } from 'react';
import { FilePlus2 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Button, InlineMessage, Progress, Text } from '@green/ui';
import { useProjects, ProjectTable } from '@/features/projects';

const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : undefined;

export const ProjectsPage: FC = () => {
  const navigate = useNavigate();
  const { query, deletion, deleteCandidate, chooseDeletion, confirmDeletion } =
    useProjects();
  const projects = query.data ?? [];
  const queryError = errorMessage(query.error);
  const error = errorMessage(deletion.error) ?? queryError;
  const newProject = () => navigate('/projects/new/import');

  return (
    <div className="flex h-dvh min-h-0 flex-col overflow-x-hidden overflow-y-auto overscroll-y-contain bg-white">
      <header className="flex min-h-13 shrink-0 flex-wrap items-center justify-between gap-3 border-b border-neutral-200 px-6 py-2">
        <Text as="strong" variant="label" className="text-sm">
          Проекты озеленения
        </Text>
        <Button
          variant="primary"
          startIcon={<FilePlus2 />}
          onClick={newProject}
        >
          Новый проект
        </Button>
      </header>
      <main className="mx-auto w-[calc(100%_-_48px)] max-w-280 py-12 pb-20">
        <Text as="h1" variant="pageHeading" className="mb-7">
          Проекты
        </Text>
        {query.isLoading && <Progress label="Загружаем проекты" />}
        {error && (
          <InlineMessage
            tone="error"
            title={
              deletion.error
                ? 'Не удалось удалить проект'
                : 'Не удалось загрузить проекты'
            }
          >
            <div className="flex min-w-0 flex-wrap items-center gap-3">
              <span className="min-w-0 flex-1 wrap-anywhere">{error}</span>
              {queryError && (
                <Button
                  variant="secondary"
                  controlSize="compact"
                  loading={query.isFetching}
                  onClick={() => void query.refetch()}
                >
                  Повторить
                </Button>
              )}
            </div>
          </InlineMessage>
        )}
        {!query.isLoading && !error && !projects.length && (
          <ProjectListEmpty onCreate={newProject} />
        )}
        {!!projects.length && (
          <ProjectTable projects={projects} onDelete={chooseDeletion} />
        )}
      </main>
      <ProjectDeletionDialog
        project={deleteCandidate}
        deleting={deletion.isPending}
        onClose={() => chooseDeletion(undefined)}
        onDelete={confirmDeletion}
      />
    </div>
  );
};
