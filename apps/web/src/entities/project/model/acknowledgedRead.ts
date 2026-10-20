import type { QueryClient } from '@tanstack/react-query';
import type { Project } from '@green/api-client';

interface ProjectReadFloor {
  stateVersion?: number;
  planVersion?: number;
}

const floorKey = (projectId: string) => ['project-write-proof', projectId];

/** Keep only the acknowledged revision floor, never a second copy of Project. */
export function acknowledgeProjectWrite(
  client: QueryClient,
  projectId: string,
  floor: ProjectReadFloor,
) {
  client.setQueryDefaults(['project-write-proof'], {
    gcTime: Number.POSITIVE_INFINITY,
  });
  client.setQueryData<ProjectReadFloor>(floorKey(projectId), (previous) => ({
    stateVersion: Math.max(
      previous?.stateVersion ?? 0,
      floor.stateVersion ?? 0,
    ),
    planVersion: Math.max(previous?.planVersion ?? 0, floor.planVersion ?? 0),
  }));
}

export function validateAcknowledgedProjectRead<T extends Project>(
  client: QueryClient,
  projectId: string,
  project: T,
) {
  const floor = client.getQueryData<ProjectReadFloor>(floorKey(projectId));
  if (
    project.id !== projectId ||
    !Number.isSafeInteger(project.state_version) ||
    (project.plan && !Number.isSafeInteger(project.plan.version)) ||
    project.state_version < (floor?.stateVersion ?? 0) ||
    (project.plan?.version ?? 0) < (floor?.planVersion ?? 0)
  )
    throw new Error(
      'Получена версия проекта старше подтверждённого сохранения. Повторите чтение.',
    );
  return project;
}
