/** Библиотека: независимые строки проектов, выбор и контекстные действия. */
import type { FC } from 'react';
import { Link } from 'react-router-dom';
import { ArrowUpRight, MoreHorizontal, Trash2 } from 'lucide-react';
import {
  Checkbox,
  IconButton,
  Menu,
  MenuItem,
  MenuPopup,
  MenuSeparator,
  MenuTrigger,
  ScrollTable,
  ScrollTableCell,
  ScrollTableColumnHeader,
  ScrollTableRow,
  Text,
} from '@green/ui';
import type { ProjectSummary } from '@green/api-client';
import {
  projectRoute,
  projectState,
} from '@/entities/project/model/projectPresentation';
import { formatFileSize, formatProjectDate } from '@/shared/format/display';

const PROJECT_COLUMNS =
  '2.5rem minmax(15rem,2fr) minmax(12rem,1fr) minmax(6rem,0.6fr) 2.5rem';

export interface ProjectTableProps {
  projects: ProjectSummary[];
  selectedIds: ReadonlySet<string>;
  removedIds: readonly string[];
  busy: boolean;
  onSelect: (id: string) => void;
  onDelete: (project: ProjectSummary) => void;
}

export const ProjectTable: FC<ProjectTableProps> = ({
  projects,
  ...rowProps
}) => (
  <ScrollTable
    aria-label="Проекты озеленения"
    columns={PROJECT_COLUMNS}
    minWidth="40rem"
    className="flex-1"
    containerClassName="rounded-card flex min-h-0 flex-1 border border-neutral-200"
    header={
      <>
        <ScrollTableColumnHeader>
          <span className="sr-only">Выбор</span>
        </ScrollTableColumnHeader>
        <ScrollTableColumnHeader>Проект</ScrollTableColumnHeader>
        <ScrollTableColumnHeader>Состояние</ScrollTableColumnHeader>
        <ScrollTableColumnHeader>Изменён</ScrollTableColumnHeader>
        <ScrollTableColumnHeader>
          <span className="sr-only">Действия</span>
        </ScrollTableColumnHeader>
      </>
    }
  >
    {projects.map((project) => (
      <ProjectRow key={project.id} project={project} {...rowProps} />
    ))}
  </ScrollTable>
);

const ProjectRow: FC<
  Omit<ProjectTableProps, 'projects'> & { project: ProjectSummary }
> = ({ project, selectedIds, removedIds, busy, onSelect, onDelete }) => {
  const state = projectState(project);
  const selected = selectedIds.has(project.id);
  const removed = removedIds.includes(project.id);
  return (
    <ScrollTableRow
      aria-selected={selected}
      data-selected={selected}
      aria-hidden={removed || undefined}
      inert={removed || undefined}
      className={
        removed
          ? 'h-0 overflow-hidden border-b-0 opacity-0 transition-[height,opacity,background-color] duration-200 motion-reduce:transition-none'
          : 'h-22 overflow-hidden transition-[height,opacity,background-color] duration-200 hover:bg-neutral-100 motion-reduce:transition-none'
      }
    >
      <ScrollTableCell>
        <Checkbox
          label={<span className="sr-only">Выбрать проект {project.name}</span>}
          checked={selected}
          disabled={busy}
          onChange={() => onSelect(project.id)}
        />
      </ScrollTableCell>
      <ScrollTableCell>
        <Link
          className="group flex min-w-0 flex-col gap-1 no-underline outline-offset-4"
          aria-disabled={busy || undefined}
          tabIndex={busy ? -1 : undefined}
          onClick={(event) => {
            if (busy) event.preventDefault();
          }}
          to={projectRoute(project)}
        >
          <Text
            variant="label"
            className="truncate text-sm group-hover:text-blue-600"
            title={project.name}
          >
            {project.name}
          </Text>
          <Text
            variant="caption"
            className="truncate"
            title={project.source_name ?? undefined}
          >
            {project.source_name ?? 'Исходник не загружен'}
            {project.source_name
              ? `, ${formatFileSize(project.source_size)}`
              : ''}
          </Text>
        </Link>
      </ScrollTableCell>
      <ScrollTableCell>
        <div className="flex min-w-0 flex-col gap-1">
          <Text variant="label" className="truncate">
            {state.title}
          </Text>
          <Text variant="caption" className="truncate" title={state.detail}>
            {state.detail}
          </Text>
        </div>
      </ScrollTableCell>
      <ScrollTableCell>
        <time
          className="text-xs text-neutral-600"
          dateTime={project.updated_at}
        >
          {formatProjectDate(project.updated_at)}
        </time>
      </ScrollTableCell>
      <ScrollTableCell>
        <Menu modal={false}>
          <MenuTrigger
            disabled={busy}
            render={
              <IconButton
                icon={<MoreHorizontal />}
                label={`Действия с проектом ${project.name}`}
                variant="ghost"
                controlSize="compact"
              />
            }
          />
          <MenuPopup align="end">
            <MenuItem
              disabled={busy}
              startIcon={<ArrowUpRight />}
              render={<Link to={projectRoute(project)} />}
            >
              Открыть проект
            </MenuItem>
            <MenuSeparator />
            <MenuItem
              disabled={busy}
              startIcon={<Trash2 />}
              className="text-error"
              onClick={() => onDelete(project)}
            >
              Удалить проект
            </MenuItem>
          </MenuPopup>
        </Menu>
      </ScrollTableCell>
    </ScrollTableRow>
  );
};
