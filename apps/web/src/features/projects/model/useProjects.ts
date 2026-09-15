import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ProjectSummary } from '@green/api-client';
import { listProjects, removeProject } from '../api/projectList';

const PROJECT_LIST_STALE_TIME = 10_000;

export function useProjects() {
  const queryClient = useQueryClient();
  const [deleteCandidate, setDeleteCandidate] = useState<ProjectSummary>();
  const deleting = useRef(false);
  const query = useQuery({
    queryKey: ['projects'],
    queryFn: listProjects,
    staleTime: PROJECT_LIST_STALE_TIME,
  });
  const deletion = useMutation({
    mutationFn: (project: ProjectSummary) => removeProject(project.id),
    onSuccess: async (_, project) => {
      setDeleteCandidate((current) =>
        current?.id === project.id ? undefined : current,
      );
      queryClient.setQueryData<ProjectSummary[]>(['projects'], (current) =>
        current?.filter((item) => item.id !== project.id),
      );
      await queryClient.invalidateQueries({ queryKey: ['projects'] });
    },
    onSettled: () => {
      deleting.current = false;
    },
  });
  const chooseDeletion = (project?: ProjectSummary) => {
    if (deleting.current) return;
    deletion.reset();
    setDeleteCandidate(project);
  };
  const confirmDeletion = () => {
    if (deleting.current || !deleteCandidate) return;
    deleting.current = true;
    deletion.mutate(deleteCandidate);
  };
  return { query, deletion, deleteCandidate, chooseDeletion, confirmDeletion };
}
