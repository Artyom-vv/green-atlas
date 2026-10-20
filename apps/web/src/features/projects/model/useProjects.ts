/** Библиотека: серверный каталог в Query, временный выбор принадлежит экрану. */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listProjects } from '../api/projectList';
import { useProjectDeletion } from './useProjectDeletion';

const PROJECT_LIST_STALE_TIME = 10_000;

export function useProjects() {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const query = useQuery({
    queryKey: ['projects'],
    queryFn: ({ signal }) => listProjects(signal),
    staleTime: PROJECT_LIST_STALE_TIME,
    networkMode: 'always',
    retry: false,
  });
  const deletion = useProjectDeletion(async () => {
    const result = await query.refetch({ throwOnError: true });
    if (!Array.isArray(result.data))
      throw new Error('Не удалось прочитать библиотеку');
    return result.data;
  });
  const projects = query.data ?? [];
  const selectedProjects = projects.filter((project) =>
    selectedIds.has(project.id),
  );
  const toggleSelection = (id: string) =>
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const toggleAll = () =>
    setSelectedIds(
      selectedProjects.length === projects.length
        ? new Set()
        : new Set(projects.map((project) => project.id)),
    );
  const clearSelection = () => setSelectedIds(new Set());
  return {
    query,
    selectedProjects,
    toggleSelection,
    toggleAll,
    clearSelection,
    ...deletion,
  };
}
