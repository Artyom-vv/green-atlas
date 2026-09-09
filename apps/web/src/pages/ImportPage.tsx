import { useRef } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Button, InlineMessage, Progress } from '@green/ui';
import { api, ApiClientError } from '@green/api-client';
import { useNavigate, useParams } from 'react-router-dom';
import { AppHeader } from '../domain-ui/AppHeader';
import { DxfUploader } from '../domain-ui/DxfUploader';
import { FlowDocument, ProjectSteps } from '../domain-ui/ProjectFlow';

export function ImportPage() {
  const navigate = useNavigate();
  const { projectId } = useParams();
  const createdProjectId = useRef<string | undefined>(undefined);
  const projectQuery = useQuery({ queryKey: ['setup-project', projectId], queryFn: () => api.getProject(projectId!, false), enabled: Boolean(projectId) });
  const sourceReadOnly = Boolean(projectQuery.data?.map_ready && projectQuery.data.plan);
  const mutation = useMutation({
    mutationFn: async (file: File) => {
      const existingId = projectId ?? createdProjectId.current;
      const project = existingId
        ? await api.getProject(existingId, false)
        : await api.createProject(file.name.replace(/\.(?:dxf|zip)$/i, '') || 'Новый проект');
      if (!project.id) throw new ApiClientError('PROJECT_ID_MISSING', 'Сервер не вернул идентификатор проекта.');
      if (project.map_ready && project.plan) throw new Error('Исходный чертёж зафиксирован. Для другого файла создайте новый проект.');
      if (!projectId) createdProjectId.current = project.id;
      return file.name.toLowerCase().endsWith('.zip')
        ? api.uploadReleaseBundle(project.id, file)
        : api.uploadDxf(project.id, file);
    },
    onSuccess: (project) => navigate(project.import_status?.mode === 'release_bundle' && project.import_status.editability === 'editable' ? `/projects/${project.id}/workspace` : `/projects/${project.id}/setup`),
  });
  const error = mutation.error instanceof ApiClientError ? mutation.error.message : mutation.error instanceof Error ? mutation.error.message : undefined;

  return (
    <div className="app-shell flow-screen">
      <AppHeader projectName={projectQuery.data?.name} />
      <main className={sourceReadOnly ? 'source-document-layout' : 'project-flow-layout import-layout'}>
        {!sourceReadOnly ? <ProjectSteps active={1} /> : null}
        <FlowDocument title={sourceReadOnly ? 'Исходный чертёж зафиксирован' : 'Добавьте исходный чертёж'} description={sourceReadOnly ? 'В проекте уже есть план. Новый исходный файл требует отдельного проекта.' : 'DXF — для нового плана. ZIP-пакет выпуска — для продолжения работы.'}>
          <div className="import-content">
            {projectId && projectQuery.isLoading ? <Progress label="Загружаем проект" /> : projectQuery.isError ? <InlineMessage tone="error">Не удалось загрузить проект. <Button variant="secondary" onClick={() => void projectQuery.refetch()}>Повторить</Button></InlineMessage> : sourceReadOnly ? <><Button variant="primary" onClick={() => navigate(`/projects/${projectId}/workspace`)}>Вернуться к плану</Button><Button variant="secondary" onClick={() => navigate('/projects/new/import')}>Новый проект с другим файлом</Button></> : <DxfUploader onUpload={(file) => mutation.mutate(file)} loading={mutation.isPending} error={error} />}
          </div>
        </FlowDocument>
      </main>
    </div>
  );
}
