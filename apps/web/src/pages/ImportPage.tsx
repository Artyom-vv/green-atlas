import { useMutation } from '@tanstack/react-query';
import { api, ApiClientError } from '@green/api-client';
import { useNavigate, useParams } from 'react-router-dom';
import { AppHeader } from '../domain-ui/AppHeader';
import { DxfUploader } from '../domain-ui/DxfUploader';
import { FlowDocument, ProjectSteps } from '../domain-ui/ProjectFlow';

export function ImportPage() {
  const navigate = useNavigate();
  const { projectId } = useParams();
  const mutation = useMutation({
    mutationFn: async (file: File) => {
      const project = projectId
        ? await api.getProject(projectId, false)
        : await api.createProject(file.name.replace(/\.dxf$/i, '') || 'Новый проект');
      if (!project.id) throw new ApiClientError('PROJECT_ID_MISSING', 'Сервер не вернул идентификатор проекта.');
      return api.uploadDxf(project.id, file);
    },
    onSuccess: (project) => navigate(`/projects/${project.id}/setup`),
  });
  const error = mutation.error instanceof ApiClientError ? mutation.error.message : mutation.error instanceof Error ? mutation.error.message : undefined;

  return (
    <div className="app-shell flow-screen">
      <AppHeader />
      <main className="project-flow-layout import-layout">
        <ProjectSteps active={1} />
        <FlowDocument title="Добавьте исходный чертёж" description={projectId ? "Новый DXF можно загрузить до начала ручной схемы." : "Загрузите DXF. Исходный файл останется без изменений."}>
          <div className="import-content">
            <DxfUploader onUpload={(file) => mutation.mutate(file)} loading={mutation.isPending} error={error} />
            <div className="document-note"><i />Исходный файл не меняется.</div>
          </div>
        </FlowDocument>
      </main>
    </div>
  );
}
