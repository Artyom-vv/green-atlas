import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import type { Project, ProjectOperation } from '@green/api-client';
import { isProjectConflict } from '@/entities/project/model/projectConflict';
import { preparationApi } from '../api/preparationApi';
import { sourceIdentity } from './sourcePreparation';
import {
  mappingReceipt,
  readPreparationEvidence,
  UnconfirmedPreparation,
  type MappingReceipt,
  type PreparationAttempt,
} from './preparationRecovery';

type PreparationPhase =
  'idle' | 'saving' | 'starting' | 'recovering' | 'unknown' | 'failed';

interface PreparationState {
  source?: string;
  phase: PreparationPhase;
  error?: unknown;
  message?: string;
}

interface PreparationCommitOptions {
  source: string;
  onProject: (project: Project) => void;
  onOperation: (operation: ProjectOperation, source: string) => void;
}

/** A saved mapping receipt outlives a failed geometry-start request. Recovery
 * performs reads only; a subsequent explicit command may retry the start. */
export function usePreparationCommit({
  source,
  onProject,
  onOperation,
}: PreparationCommitOptions) {
  const mounted = useRef(true);
  const currentSource = useRef(source);
  const gate = useRef(false);
  const attempt = useRef<PreparationAttempt | undefined>(undefined);
  const receipt = useRef<MappingReceipt | undefined>(undefined);
  const unresolved = useRef(false);
  const [state, setState] = useState<PreparationState>({ phase: 'idle' });
  useLayoutEffect(() => {
    currentSource.current = source;
    if (!gate.current && attempt.current?.source !== source) {
      unresolved.current = false;
      receipt.current = undefined;
      attempt.current = undefined;
    }
  }, [source]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const publish = (next: PreparationState) => {
    if (mounted.current) setState(next);
  };

  const recover = useCallback(async () => {
    const captured = attempt.current;
    if (gate.current || !captured) return;
    gate.current = true;
    if (mounted.current)
      setState((current) => ({ ...current, phase: 'recovering' }));
    try {
      const evidence = await readPreparationEvidence(
        preparationApi,
        captured,
        receipt.current,
      );
      receipt.current = evidence.receipt;
      onProject(evidence.project);
      if (mounted.current && evidence.operation)
        onOperation(evidence.operation, captured.source);
      unresolved.current = false;
      if (mounted.current) setState({ phase: 'idle', source: captured.source });
    } catch (error) {
      unresolved.current = true;
      if (mounted.current)
        setState((current) => ({
          ...current,
          source: captured.source,
          phase: 'unknown',
          message:
            error instanceof UnconfirmedPreparation
              ? error.message
              : 'Не удалось прочитать состояние подготовки карты. Повторите проверку соединения.',
        }));
    } finally {
      gate.current = false;
      if (currentSource.current !== captured.source) unresolved.current = false;
    }
  }, [onOperation, onProject]);

  const mutate = (request: PreparationAttempt) => {
    if (gate.current || unresolved.current) return;
    gate.current = true;
    attempt.current = request;
    const reusable =
      receipt.current?.source === request.source &&
      receipt.current?.draftKey === request.draftKey &&
      receipt.current.stateVersion === request.baseStateVersion;
    if (!reusable) receipt.current = undefined;
    publish({
      source: request.source,
      phase: reusable ? 'starting' : 'saving',
    });
    void (async () => {
      try {
        if (!reusable) {
          const saved = await preparationApi.saveMappings(
            request.projectId,
            request.mappings,
          );
          receipt.current = mappingReceipt(request, saved);
          if (sourceIdentity(saved) === request.source) onProject(saved);
        }
        publish({ source: request.source, phase: 'starting' });
        const operation = await preparationApi.startGeometryOperation(
          request.projectId,
        );
        // A successful start response identifies an acknowledged operation;
        // lost responses use the stricter basis matching in read recovery.
        if (mounted.current) onOperation(operation, request.source);
        publish({ source: request.source, phase: 'idle' });
      } catch (error) {
        const rejectedBeforeSave = !receipt.current && isProjectConflict(error);
        unresolved.current = !rejectedBeforeSave;
        publish({
          source: request.source,
          phase: rejectedBeforeSave ? 'failed' : 'unknown',
          error,
          message: receipt.current
            ? 'Настройки сохранены. Не удалось подтвердить запуск расчёта. Сначала прочитайте его состояние.'
            : 'Не удалось подтвердить сохранение настроек. Проверяем актуальное состояние проекта.',
        });
      } finally {
        gate.current = false;
        if (currentSource.current !== request.source)
          unresolved.current = false;
      }
    })();
  };

  const reset = () => {
    if (gate.current) return;
    unresolved.current = false;
    receipt.current = undefined;
    attempt.current = undefined;
    publish({ phase: 'idle' });
  };
  const visible = state.source === source ? state : { phase: 'idle' as const };
  return {
    ...visible,
    isPending: ['saving', 'starting', 'recovering'].includes(visible.phase),
    needsRecovery:
      visible.phase === 'unknown' || visible.phase === 'recovering',
    mutate,
    recover,
    reset,
  };
}
