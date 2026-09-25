import type { FC } from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight, Trash2 } from 'lucide-react';
import { DataTable, LinkIconButton, IconButton, Text } from '@green/ui';
import type { ProjectSummary } from '@green/api-client';
import {
  projectRoute,
  projectState,
} from '@/entities/project/model/projectPresentation';
import { formatFileSize, formatProjectDate } from '@/shared/format/display';

export interface ProjectTableProps {
  projects: ProjectSummary[];
  onDelete: (project: ProjectSummary) => void;
}

export const ProjectTable: FC<ProjectTableProps> = ({ projects, onDelete }) => (
  <DataTable className="min-w-160 table-fixed border-t border-neutral-300">
    <thead>
      <tr>
        <th className="w-[44%]">Проект</th>
        <th className="w-[24%]">Состояние</th>
        <th>Изменён</th>
        <th className="w-24">
          <span className="sr-only">Действия</span>
        </th>
      </tr>
    </thead>
    <tbody>
      {projects.map((project) => (
        <ProjectRow key={project.id} project={project} onDelete={onDelete} />
      ))}
    </tbody>
  </DataTable>
);

interface ProjectRowProps {
  project: ProjectSummary;
  onDelete: ProjectTableProps['onDelete'];
}

const ProjectRow: FC<ProjectRowProps> = ({ project, onDelete }) => {
  const state = projectState(project);
  return (
    <tr className="h-17 hover:bg-neutral-100">
      <td>
        <Link
          className="flex min-w-0 flex-col gap-1 no-underline"
          to={projectRoute(project)}
        >
          <Text variant="label" className="truncate text-sm">
            {project.name}
          </Text>
          <Text variant="caption" className="truncate">
            {project.source_name ?? 'Исходник не загружен'}
            {project.source_name
              ? `, ${formatFileSize(project.source_size)}`
              : ''}
          </Text>
        </Link>
      </td>
      <td>
        <div className="flex flex-col gap-1">
          <Text variant="label">{state.title}</Text>
          <Text variant="caption">{state.detail}</Text>
        </div>
      </td>
      <td>
        <time
          className="text-xs text-neutral-600"
          dateTime={project.updated_at}
        >
          {formatProjectDate(project.updated_at)}
        </time>
      </td>
      <td>
        <div className="flex flex-wrap items-center justify-end gap-1">
          <IconButton
            icon={<Trash2 />}
            label={`Удалить ${project.name}`}
            variant="danger"
            controlSize="compact"
            onClick={() => onDelete(project)}
          />
          <LinkIconButton
            render={<Link to={projectRoute(project)} />}
            icon={<ArrowRight />}
            label={`Открыть ${project.name}`}
            variant="ghost"
            controlSize="compact"
          />
        </div>
      </td>
    </tr>
  );
};
