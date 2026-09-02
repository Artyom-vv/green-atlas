import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiClientError, type ProjectSummary } from '@green/api-client';
import { ArrowRight, FilePlus2, FolderOpen, Trash2 } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { Button, Dialog, IconButton, InlineMessage } from '@green/ui';
import { countLabel } from '../domain-ui/countLabel';

const date = (value: string) => new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(value));
const fileSize = (value?: number | null) => value ? `${(value / 1024 / 1024).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} МБ` : '—';
function projectState(project: ProjectSummary) {
  if (!project.source_name) return { title: 'Нужен DXF', detail: 'Исходник не загружен' };
  if (!project.has_geometry) return { title: 'Подготовьте карту', detail: 'Проверьте слои DXF' };
  if (!project.planting_zone_count) return { title: 'Выберите участки', detail: 'Карта подготовлена' };
  const detail = `${countLabel(project.plan_object_count, 'посадка', 'посадки', 'посадок')}, ${countLabel(project.planting_zone_count, 'участок', 'участка', 'участков')}`;
  return { title: project.plan_object_count ? 'Редактируется' : 'Готов к размещению', detail };
}

function projectRoute(project: ProjectSummary) {
  if (!project.source_name) return `/projects/${project.id}/import`;
  if (!project.has_geometry && ['imported', 'mapped'].includes(project.status)) return `/projects/${project.id}/setup`;
  return `/projects/${project.id}/workspace`;
}

export function ProjectsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [deleteCandidate, setDeleteCandidate] = useState<ProjectSummary>();
  const projectsQuery = useQuery({ queryKey: ['projects'], queryFn: () => api.listProjects(), staleTime: 10_000 });
  const refreshProjects = () => queryClient.invalidateQueries({ queryKey: ['projects'] });
  const deleteProject = useMutation({ mutationFn: (project: ProjectSummary) => api.deleteProject(project.id), onSuccess: async () => { setDeleteCandidate(undefined); await refreshProjects(); } });
  const queryError = projectsQuery.error instanceof ApiClientError ? projectsQuery.error.message : projectsQuery.error instanceof Error ? projectsQuery.error.message : undefined;
  const mutationError = deleteProject.error;
  const error = mutationError instanceof ApiClientError ? mutationError.message : mutationError instanceof Error ? mutationError.message : queryError;
  const projects = projectsQuery.data ?? [];

  return (
    <div className="app-shell projects-screen">
      <header className="app-header projects-topbar"><strong>Проекты озеленения</strong><div className="projects-topbar__actions"><Button variant="primary" icon={FilePlus2} onClick={() => navigate('/projects/new/import')}>Новый проект</Button></div></header>
      <main className="projects-page">
        <header className="projects-heading"><div><h1>Проекты</h1><p>DXF и ручные посадки сохраняются автоматически</p></div></header>
        {projectsQuery.isLoading ? <div className="projects-state">Загружаем проекты</div> : null}
        {error ? <InlineMessage tone="error" title="Не удалось загрузить проекты"><div className="projects-query-error"><span>{error}</span>{queryError ? <Button variant="secondary" controlSize="compact" loading={projectsQuery.isFetching} onClick={() => void projectsQuery.refetch()}>Повторить</Button> : null}</div></InlineMessage> : null}
        {!projectsQuery.isLoading && !error && !projects.length ? <div className="projects-empty"><FolderOpen size={24} /><strong>Проектов пока нет</strong><span>Загрузите DXF, чтобы подготовить первый план озеленения.</span><Button variant="primary" icon={FilePlus2} onClick={() => navigate('/projects/new/import')}>Загрузить DXF</Button></div> : null}
        {projects.length ? <div className="projects-table-wrap"><table className="projects-table"><thead><tr><th>Проект</th><th>Состояние</th><th>Изменён</th><th><span className="sr-only">Действия</span></th></tr></thead><tbody>{projects.map((project) => { const state = projectState(project); return <tr key={project.id}><td><Link className="project-name" to={projectRoute(project)}><strong>{project.name}</strong><span>{project.source_name ?? 'DXF не загружен'}{project.source_name ? `, ${fileSize(project.source_size)}` : ''}</span></Link></td><td><span className="project-state"><strong>{state.title}</strong><small>{state.detail}</small></span></td><td><time dateTime={project.updated_at}>{date(project.updated_at)}</time></td><td><div className="project-row-actions"><IconButton icon={Trash2} label={`Удалить ${project.name}`} variant="danger" onClick={() => setDeleteCandidate(project)} /><Link className="project-open" to={projectRoute(project)} aria-label={`Открыть ${project.name}`}><ArrowRight size={16} /></Link></div></td></tr>; })}</tbody></table></div> : null}
      </main>
      <Dialog open={Boolean(deleteCandidate)} title="Удалить проект навсегда?" onClose={() => setDeleteCandidate(undefined)} footer={<><Button variant="secondary" onClick={() => setDeleteCandidate(undefined)} disabled={deleteProject.isPending}>Отмена</Button><Button variant="danger" icon={Trash2} loading={deleteProject.isPending} onClick={() => deleteCandidate && deleteProject.mutate(deleteCandidate)}>Удалить</Button></>}><p>Проект «{deleteCandidate?.name}», исходный DXF, планы и результаты проверки будут удалены без возможности восстановления.</p></Dialog>
    </div>
  );
}
