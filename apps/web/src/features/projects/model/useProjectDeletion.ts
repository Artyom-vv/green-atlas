/** Удаление в библиотеке: последовательная запись и восстановление без повторов. */
import { useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import {
  ApiClientError,
  isUnsentLocalRequest,
  type ProjectSummary,
} from '@green/api-client';
import { useProjectWorkContribution } from '@/features/project-navigation/model/ProjectWorkContext';
import type { WriteOutcome } from '@/shared/model/writeOutcome';
import { removeProject } from '../api/projectList';

const refused = (error: unknown) =>
  isUnsentLocalRequest(error) ||
  (error instanceof ApiClientError &&
    [
      'BAD_REQUEST',
      'VALIDATION_ERROR',
      'NOT_FOUND',
      'PROJECT_VERSION_CONFLICT',
    ].includes(error.code));

export interface ProjectDeletionProgress {
  total: number;
  currentName: string;
  completedIds: string[];
}

export function useProjectDeletion(
  readFreshList: () => Promise<ProjectSummary[]>,
) {
  const [deleteCandidates, setDeleteCandidates] = useState<ProjectSummary[]>(
    [],
  );
  const [progress, setProgress] = useState<ProjectDeletionProgress>();
  const [writeOutcome, setWriteOutcome] = useState<WriteOutcome>('idle');
  const [readRecovery, setReadRecovery] = useState(false);
  const [recoveryError, setRecoveryError] = useState<unknown>();
  const captured = useRef<string[]>([]);
  const gate = useRef(false);
  const deletion = useMutation({
    networkMode: 'always',
    retry: false,
    mutationFn: async (projects: ProjectSummary[]) => {
      if (gate.current) throw new Error('Сначала проверьте результат удаления');
      if (!projects.length) return;
      gate.current = true;
      captured.current = [];
      setWriteOutcome('writing');
      setRecoveryError(undefined);
      const completedIds: string[] = [];
      for (const project of projects) {
        setProgress({
          total: projects.length,
          currentName: project.name,
          completedIds: [...completedIds],
        });
        captured.current.push(project.id);
        try {
          await removeProject(project);
        } catch (error) {
          const rejected = refused(error);
          // Подтверждённый отказ не участвует в проверке отсутствия записи.
          if (rejected) captured.current.pop();
          const needsRead = !rejected || completedIds.length > 0;
          gate.current = needsRead;
          setWriteOutcome(
            rejected ? (completedIds.length ? 'confirmed' : 'idle') : 'unknown',
          );
          setReadRecovery(needsRead);
          throw error;
        }
        completedIds.push(project.id);
        setProgress({
          total: projects.length,
          currentName: project.name,
          completedIds: [...completedIds],
        });
      }
      setWriteOutcome('confirmed');
      setDeleteCandidates([]);
      try {
        await readFreshList();
        gate.current = false;
        setReadRecovery(false);
      } catch (error) {
        setReadRecovery(true);
        setRecoveryError(error);
      }
    },
  });
  const recovery = useMutation({
    networkMode: 'always',
    retry: false,
    mutationFn: async () => {
      const ids = captured.current;
      if (!ids.length)
        throw new Error('Нет отправленного удаления для проверки');
      const fresh = await readFreshList();
      // Актуальный каталог подтверждает отсутствие проектов, а не авторство записи.
      if (fresh.some((project) => ids.includes(project.id)))
        throw new Error('Удаление ещё не подтверждено библиотекой');
    },
    onSuccess: () => {
      gate.current = false;
      captured.current = [];
      setWriteOutcome('idle');
      setReadRecovery(false);
      setRecoveryError(undefined);
      setDeleteCandidates([]);
      setProgress(undefined);
      deletion.reset();
    },
    onError: setRecoveryError,
  });
  const chooseDeletion = (projects: ProjectSummary[] = []) => {
    if (gate.current) return;
    deletion.reset();
    setWriteOutcome('idle');
    setRecoveryError(undefined);
    setProgress(undefined);
    setDeleteCandidates(projects.map((project) => ({ ...project })));
  };
  const confirmDeletion = () => {
    if (gate.current || !deleteCandidates.length) return;
    deletion.mutate(deleteCandidates);
  };
  useProjectWorkContribution('library-deletion', {
    writeOutcome,
    readRecovery,
    editingBlocked: writeOutcome === 'unknown' || readRecovery,
  });
  return {
    deletion,
    recovery,
    deleteCandidates,
    progress,
    chooseDeletion,
    confirmDeletion,
    writeOutcome,
    readRecovery,
    recoveryError,
  };
}
