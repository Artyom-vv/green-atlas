import { Button, InlineMessage, Progress } from '@green/ui';
import type {
  ProjectImportOptions,
  useProjectImport,
} from '../useProjectImport';
import { ProjectImportChoices } from './ProjectImportChoices';

interface Props extends ProjectImportOptions {
  importer: ReturnType<typeof useProjectImport>;
  initialSource?: string | null;
}

export function ProjectImportContent({
  importer,
  initialSource,
  ...options
}: Props) {
  if (importer.loadingProject) return <Progress label="Загружаем проект" />;
  if (importer.projectError)
    return (
      <InlineMessage tone="error">
        <div className="flex flex-wrap items-center gap-3">
          <span>Не удалось загрузить проект.</span>
          <Button onClick={() => void importer.retryProject()}>
            Повторить
          </Button>
        </div>
      </InlineMessage>
    );
  if (importer.sourceReadOnly)
    return (
      <div className="flex flex-wrap gap-3">
        <Button
          variant="primary"
          onClick={() =>
            options.onNavigate(`/projects/${options.projectId}/workspace`)
          }
        >
          Вернуться к плану
        </Button>
        <Button onClick={() => options.onNavigate('/projects/new/import')}>
          Новый проект с другим файлом
        </Button>
      </div>
    );
  return (
    <ProjectImportChoices
      {...options}
      projectStateVersion={importer.project?.state_version}
      hasSource={Boolean(importer.project?.source_file)}
      initialSource={initialSource}
      onUpload={importer.upload}
      loading={importer.uploading}
      error={importer.error}
    />
  );
}
