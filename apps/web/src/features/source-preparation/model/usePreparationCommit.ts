import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { flushSync } from 'react-dom';
import {
  ApiClientError,
  isUnsentLocalRequest,
  type Project,
  type ProjectRead,
  type ProjectOperation,
} from '@green/api-client';
import type { WriteOutcome } from '@/shared/model/writeOutcome';
import { PREPARATION_MESSAGES } from '@/entities/source-data/config/sourceMessages';
import { preparationApi } from '../api/preparationApi';
import { layerMappings, sourceIdentity } from './sourcePreparation';
import {
  mappingReceipt,
  mappingKey,
  canContinuePreparedProject,
  readPreparationEvidence,
  UnconfirmedPreparation,
  type MappingReceipt,
  type PreparationAttempt,
} from './preparationRecovery';

type PreparationPhase =
  | 'idle'
  | 'saving'
  | 'reading'
  | 'starting'
  | 'recovering'
  | 'recovery'
  | 'unknown'
  | 'failed';
interface PreparationState {
  source?: string;
  phase: PreparationPhase;
  writeOutcome: WriteOutcome;
  acknowledgedDraftKey?: string;
  error?: unknown;
  message?: string;
}
interface PreparationCommitOptions {
  source: string;
  onProject: (project: Project) => void;
  onOperation: (operation: ProjectOperation, source: string) => void;
  onContinue?: (project: Project) => void;
}
const emptyState: PreparationState = { phase: 'idle', writeOutcome: 'idle' };
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

/** Один владелец записи назначений. Восстановление читает захваченный
 * исходник и настройки, не повторяя запись с неизвестным результатом */
export function usePreparationCommit({
  source,
  onProject,
  onOperation,
  onContinue,
}: PreparationCommitOptions) {
  const mounted = useRef(true);
  const currentSource = useRef(source);
  const gate = useRef(false);
  const attempt = useRef<PreparationAttempt | undefined>(undefined);
  const receipt = useRef<MappingReceipt | undefined>(undefined);
  const operationAcknowledged = useRef(false);
  const acknowledgedOperationId = useRef<string | undefined>(undefined);
  const unresolved = useRef(false);
  const [state, setState] = useState<PreparationState>(emptyState);
  useLayoutEffect(() => {
    currentSource.current = source;
    if (
      !gate.current &&
      !unresolved.current &&
      attempt.current?.source !== source
    ) {
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
  const publish = useCallback((next: PreparationState) => {
    if (mounted.current) setState(next);
  }, []);
  const proven = (captured: PreparationAttempt) =>
    Boolean(
      receipt.current &&
      (captured.mode === 'save' ||
        captured.mode === 'continue' ||
        operationAcknowledged.current),
    );
  const finishLocalRead = useCallback(
    (captured: PreparationAttempt, project: Project) => {
      const continueHere =
        canContinuePreparedProject(project) &&
        mounted.current &&
        currentSource.current === captured.source;
      const finish = () =>
        publish({
          phase: 'idle',
          source: captured.source,
          writeOutcome: 'idle',
          acknowledgedDraftKey: captured.draftKey,
        });
      if (continueHere && onContinue) {
        // The shared leave guard must observe the saved form before navigation.
        flushSync(finish);
        onContinue(project);
      } else finish();
    },
    [onContinue, publish],
  );

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
      operationAcknowledged.current = true;
      acknowledgedOperationId.current = evidence.operation?.id;
      onProject(evidence.project);
      if (mounted.current && evidence.operation)
        onOperation(evidence.operation, captured.source);
      unresolved.current = false;
      if (captured.mode === 'continue') {
        finishLocalRead(captured, evidence.project);
        return;
      }
      publish({
        phase: 'idle',
        source: captured.source,
        writeOutcome: 'idle',
        acknowledgedDraftKey: captured.draftKey,
      });
      return evidence.project;
    } catch (error) {
      unresolved.current = true;
      const confirmed = proven(captured);
      if (mounted.current)
        setState((current) => ({
          ...current,
          source: captured.source,
          phase: confirmed ? 'recovery' : 'unknown',
          writeOutcome: confirmed ? 'confirmed' : 'unknown',
          acknowledgedDraftKey: receipt.current?.draftKey,
          message:
            error instanceof UnconfirmedPreparation
              ? error.message
              : captured.mode === 'save'
                ? confirmed
                  ? PREPARATION_MESSAGES.mappingsReadRecovery
                  : PREPARATION_MESSAGES.mappingsSaveUnknown
                : confirmed
                  ? 'Настройки сохранены, но актуальное состояние пока не прочитано. Можно выйти; перед редактированием повторите чтение.'
                  : 'Не удалось прочитать состояние подготовки. Повторите проверку результата; запись повторно не отправляется.',
        }));
    } finally {
      gate.current = false;
    }
  }, [finishLocalRead, onOperation, onProject, publish]);

  const execute = async (
    request: PreparationAttempt,
    confirmRead = false,
  ): Promise<Project | undefined> => {
    if (gate.current || unresolved.current)
      throw new Error('Сначала подтвердите результат предыдущей записи.');
    gate.current = true;
    attempt.current = request;
    operationAcknowledged.current = false;
    acknowledgedOperationId.current = undefined;
    const reusable =
      receipt.current?.source === request.source &&
      receipt.current?.draftKey === request.draftKey &&
      receipt.current.stateVersion === request.baseStateVersion;
    if (!reusable) receipt.current = undefined;
    publish({
      source: request.source,
      phase: reusable && request.mode !== 'save' ? 'starting' : 'saving',
      writeOutcome: 'writing',
    });
    let saved: Project | undefined;
    let startSent = false;
    try {
      if (!reusable) {
        saved = await preparationApi.saveMappings(
          request.projectId,
          request.writeMappings ?? request.mappings,
        );
        receipt.current = mappingReceipt(request, saved);
        publish({
          source: request.source,
          phase: 'saving',
          writeOutcome: 'confirmed',
          acknowledgedDraftKey: request.draftKey,
        });
        if (sourceIdentity(saved) === request.source && !confirmRead)
          onProject(saved);
      }
      if (request.mode === 'save') {
        if (confirmRead) {
          publish({
            source: request.source,
            phase: 'reading',
            writeOutcome: 'confirmed',
            acknowledgedDraftKey: request.draftKey,
          });
          const evidence = await readPreparationEvidence(
            preparationApi,
            request,
            receipt.current,
          );
          receipt.current = evidence.receipt;
          saved = evidence.project;
          onProject(saved);
        }
        publish({
          source: request.source,
          phase: 'idle',
          writeOutcome: 'idle',
          acknowledgedDraftKey: request.draftKey,
        });
        return saved;
      }
      if (request.mode === 'continue') {
        publish({
          source: request.source,
          phase: 'reading',
          writeOutcome: 'confirmed',
          acknowledgedDraftKey: request.draftKey,
        });
        const evidence = await readPreparationEvidence(
          preparationApi,
          request,
          receipt.current,
        );
        receipt.current = evidence.receipt;
        onProject(evidence.project);
        finishLocalRead(request, evidence.project);
        return evidence.project;
      }
      publish({
        source: request.source,
        phase: 'starting',
        writeOutcome: 'writing',
        acknowledgedDraftKey: request.draftKey,
      });
      startSent = true;
      const operation = await (request.mode === 'capture'
        ? preparationApi.startCapturePreparation(request.projectId)
        : preparationApi.startGeometryOperation(request.projectId));
      operationAcknowledged.current = true;
      acknowledgedOperationId.current = operation.id;
      if (mounted.current) onOperation(operation, request.source);
      publish({
        source: request.source,
        phase: 'idle',
        writeOutcome: 'idle',
        acknowledgedDraftKey: request.draftKey,
      });
      return saved;
    } catch (error) {
      const rejected = refused(error);
      const confirmed =
        proven(request) || Boolean(receipt.current && startSent && rejected);
      unresolved.current = !rejected;
      publish({
        source: request.source,
        phase: rejected ? 'failed' : confirmed ? 'recovery' : 'unknown',
        writeOutcome: confirmed ? 'confirmed' : rejected ? 'idle' : 'unknown',
        acknowledgedDraftKey: receipt.current?.draftKey,
        error,
        message:
          request.mode === 'save' && receipt.current
            ? PREPARATION_MESSAGES.mappingsReadRecovery
            : request.mode === 'continue' && receipt.current
              ? error instanceof UnconfirmedPreparation
                ? error.message
                : 'Настройки сохранены, но актуальный снимок не прочитан. Повторите чтение; подготовка AutoCAD не запускается.'
              : receipt.current
                ? 'Настройки сохранены. Запуск подготовки не подтверждён. Сначала прочитайте его состояние.'
                : PREPARATION_MESSAGES.mappingsSaveUnknown,
      });
      throw error;
    } finally {
      gate.current = false;
    }
  };
  const mutate = (request: PreparationAttempt) => {
    if (gate.current || unresolved.current) return;
    void execute(request).catch(() => undefined);
  };
  const saveOnly = async (
    request: PreparationAttempt,
    options: { confirmRead?: boolean; recoverLostResponse?: boolean } = {},
  ) => {
    try {
      return await execute({ ...request, mode: 'save' }, options.confirmRead);
    } catch (error) {
      if (options.recoverLostResponse && unresolved.current) {
        const project = await recover();
        if (project) return project;
      }
      throw error;
    }
  };
  const confirmCaptureRead = (
    project: ProjectRead,
    operation: ProjectOperation,
    draftKey: string,
  ) => {
    const captured = attempt.current;
    const previous = receipt.current;
    if (
      gate.current ||
      unresolved.current ||
      !mounted.current ||
      captured?.mode !== 'capture' ||
      !previous ||
      currentSource.current !== captured.source ||
      operation.kind !== 'prepare_cad_capture' ||
      operation.status !== 'completed' ||
      !operation.id ||
      operation.id !== acknowledgedOperationId.current ||
      operation.project_id !== captured.projectId ||
      operation.project_state_version !== previous.stateVersion ||
      project.id !== captured.projectId ||
      sourceIdentity(project) !== captured.source ||
      project.prepared_state !== 'available' ||
      !Number.isSafeInteger(project.state_version) ||
      project.state_version < previous.stateVersion ||
      draftKey !== captured.draftKey ||
      mappingKey(Object.values(layerMappings(project.layers ?? []))) !==
        captured.draftKey
    )
      return false;
    // Подготовка могла сохранить рецепт и поднять ревизию. Свежий результат
    // той же операции подтверждает прежние назначения без второго PUT.
    receipt.current = mappingReceipt(captured, project, 'current_snapshot');
    return true;
  };
  const reset = () => {
    if (gate.current || unresolved.current) return;
    receipt.current = undefined;
    attempt.current = undefined;
    publish(emptyState);
  };
  const visible =
    state.source === source ||
    state.writeOutcome === 'unknown' ||
    state.phase === 'recovery'
      ? state
      : emptyState;
  return {
    ...visible,
    isPending: ['saving', 'reading', 'starting', 'recovering'].includes(
      visible.phase,
    ),
    pendingLabel:
      visible.phase === 'saving'
        ? 'Сохраняем изменения'
        : visible.phase === 'reading'
          ? 'Читаем проект'
          : undefined,
    readRecovery: ['recovering', 'recovery', 'unknown'].includes(visible.phase),
    needsRecovery: ['unknown', 'recovering', 'recovery'].includes(
      visible.phase,
    ),
    mutate,
    saveOnly,
    recover,
    confirmCaptureRead,
    reset,
  };
}
