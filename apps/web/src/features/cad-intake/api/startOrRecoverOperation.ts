import {
  api,
  type OperationKind,
  type ProjectOperation,
} from '@green/api-client';
import { operationActive } from '@/entities/operation/model/operationPresentation';

/** Recover a lost receipt without treating an old completed job as a new one. */
export async function startOrRecoverOperation(
  projectId: string,
  kind: OperationKind,
  matches: (operation: ProjectOperation) => boolean,
  start: () => Promise<ProjectOperation>,
) {
  const previous = await api.getLatestOperation(projectId, kind);
  try {
    return await start();
  } catch (error) {
    const recovered = await api
      .getLatestOperation(projectId, kind)
      .catch(() => null);
    if (
      recovered &&
      matches(recovered) &&
      (recovered.id !== previous?.id ||
        (previous && operationActive(previous))) &&
      (operationActive(recovered) || recovered.status === 'completed')
    )
      return recovered;
    throw error;
  }
}
