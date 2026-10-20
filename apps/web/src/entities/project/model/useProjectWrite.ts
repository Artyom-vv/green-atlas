/** Общая запись параметров проекта: подтверждение ревизии, сохранность
 * при уходе и восстановление чтением после потерянного ответа. */
import { useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { useQueryClient } from '@tanstack/react-query';
import {
  ApiClientError,
  api,
  isUnsentLocalRequest,
  type Project,
} from '@green/api-client';
import {
  acknowledgeProjectWrite,
  validateAcknowledgedProjectRead,
} from '@/entities/project/model/acknowledgedRead';
import type { WriteOutcome } from '@/shared/model/writeOutcome';

/** Идентичность исходника отделена от изменяемых назначений и параметров плана. */
export const projectSourceIdentity = (project?: Project) =>
  JSON.stringify([
    project?.id,
    project?.source_file?.imported_at,
    project?.source_file?.name,
    project?.source_file?.size,
    project?.source_file?.content_sha256,
  ]);

interface WriteRequest {
  send: () => Promise<Project>;
  matches: (project: Project) => boolean;
}
interface CapturedWrite extends WriteRequest {
  projectId: string;
  source: string;
  baseStateVersion: number;
  acknowledgedStateVersion?: number;
}
interface State {
  writeOutcome: WriteOutcome;
  readRecovery: boolean;
  error?: unknown;
}
const idle: State = { writeOutcome: 'idle', readRecovery: false };
const refused = (error: unknown) =>
  isUnsentLocalRequest(error) ||
  (error instanceof ApiClientError &&
    [
      'BAD_REQUEST',
      'VALIDATION_ERROR',
      'NOT_FOUND',
      'VERSION_CONFLICT',
      'PROJECT_VERSION_CONFLICT',
    ].includes(error.code));

/** Запись параметров проекта с подтверждением версии и чтением после потерянного
 * ответа. Восстановление читает сохранённое состояние без повторной записи. */
export function useProjectWrite(
  project: Project | undefined,
  onProject: (project: Project) => void,
) {
  const client = useQueryClient();
  const gate = useRef(false);
  const captured = useRef<CapturedWrite | undefined>(undefined);
  const unresolved = useRef(false);
  const [state, setState] = useState<State>(idle);
  const publish = (updated: Project) => {
    acknowledgeProjectWrite(client, updated.id!, {
      stateVersion: updated.state_version,
      planVersion: updated.plan?.version,
    });
    onProject(validateAcknowledgedProjectRead(client, updated.id!, updated));
  };
  const execute = async (request: WriteRequest) => {
    if (!project?.id || gate.current || unresolved.current)
      throw new Error('Сначала подтвердите результат предыдущей записи.');
    gate.current = true;
    const accepted: CapturedWrite = {
      ...request,
      projectId: project.id,
      source: projectSourceIdentity(project),
      baseStateVersion: project.state_version,
    };
    captured.current = accepted;
    setState({ writeOutcome: 'writing', readRecovery: false });
    try {
      const updated = await request.send();
      if (
        updated.id !== accepted.projectId ||
        projectSourceIdentity(updated) !== accepted.source ||
        !Number.isSafeInteger(updated.state_version) ||
        updated.state_version < accepted.baseStateVersion
      ) {
        throw new Error('Ответ не подтверждает актуальную версию проекта.');
      }
      accepted.acknowledgedStateVersion = updated.state_version;
      setState({ writeOutcome: 'confirmed', readRecovery: true });
      publish(updated);
      // Переход использует подтверждение записи из общей политики сохранности.
      flushSync(() =>
        setState({ writeOutcome: 'confirmed', readRecovery: false }),
      );
      return updated;
    } catch (error) {
      const confirmed = accepted.acknowledgedStateVersion !== undefined;
      unresolved.current = confirmed || !refused(error);
      setState({
        writeOutcome: confirmed
          ? 'confirmed'
          : refused(error)
            ? 'idle'
            : 'unknown',
        readRecovery: unresolved.current,
        error,
      });
      throw error;
    } finally {
      gate.current = false;
    }
  };
  const recover = async () => {
    const accepted = captured.current;
    if (!accepted || gate.current) return;
    gate.current = true;
    setState((current) => ({ ...current, readRecovery: true }));
    try {
      const updated = validateAcknowledgedProjectRead(
        client,
        accepted.projectId,
        await api.getProjectRead(accepted.projectId, false),
      );
      const confirmed = accepted.acknowledgedStateVersion !== undefined;
      if (
        projectSourceIdentity(updated) !== accepted.source ||
        updated.state_version <
          (accepted.acknowledgedStateVersion ?? accepted.baseStateVersion) ||
        (!confirmed && !accepted.matches(updated))
      ) {
        throw new Error(
          'Свежее состояние не подтверждает отправленное решение. Запись повторно не отправляется.',
        );
      }
      publish(updated);
      unresolved.current = false;
      setState(idle);
    } catch (error) {
      setState({
        writeOutcome:
          accepted.acknowledgedStateVersion !== undefined
            ? 'confirmed'
            : 'unknown',
        readRecovery: true,
        error,
      });
    } finally {
      gate.current = false;
    }
  };
  return {
    ...state,
    execute,
    recover,
    blocked:
      state.writeOutcome === 'writing' ||
      state.writeOutcome === 'unknown' ||
      state.readRecovery,
  };
}
