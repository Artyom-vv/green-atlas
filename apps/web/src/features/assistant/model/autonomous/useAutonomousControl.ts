import {
  acknowledgeControlReceipt,
  confirmedControlResult,
  controlIdentity,
  performAgentControl,
  type AgentMapControl,
} from '@/features/assistant/model/autonomous/autonomousControl';
import { api, type AgentRun } from '@green/api-client';
import { useEffect, useRef, useState } from 'react';

export function useAutonomousControl(
  run: AgentRun | undefined,
  enabled: boolean,
  adapter: AgentMapControl | undefined,
  accept: (run: AgentRun) => void,
) {
  const [error, setError] = useState<string>();
  const [acknowledgementPending, setAcknowledgementPending] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const controller = useRef<AbortController | undefined>(undefined);
  const callbacks = useRef({ adapter, accept });
  callbacks.current = { adapter, accept };
  const command =
    run?.state.status === 'waiting_ui' ? run.state.control_command : undefined;
  const identity = command ? controlIdentity(command) : undefined;
  const commandRef = useRef(command);
  commandRef.current = command;
  const runRef = useRef(run);
  runRef.current = run;

  useEffect(() => {
    setError(undefined);
    setAcknowledgementPending(false);
    if (!identity || !enabled) return;
    const command = commandRef.current;
    const original = runRef.current;
    if (
      !command ||
      !original ||
      command.project_id !== original.state.project_id ||
      command.run_id !== original.state.run_id ||
      command.execution_attempt_id !== original.state.execution_attempt_id
    )
      return;
    const operation = new AbortController();
    controller.current = operation;
    let submitted = false;
    const acceptCurrent = (current: AgentRun) => {
      if (
        !operation.signal.aborted &&
        current.state.project_id === command.project_id &&
        current.state.run_id === command.run_id &&
        current.revision >= original.revision
      )
        callbacks.current.accept(current);
    };
    void (async () => {
      try {
        // Always re-read ownership before consuming a persisted command from history/reload.
        const current = await api.getAgentRun(
          command.project_id,
          command.run_id,
        );
        if (operation.signal.aborted) return;
        if (
          current.state.status !== 'waiting_ui' ||
          !current.state.control_command ||
          controlIdentity(current.state.control_command) !== identity
        ) {
          acceptCurrent(current);
          return;
        }
        const [proof, project] = await Promise.all([
          api.getAgentControlCommand(
            command.project_id,
            command.run_id,
            operation.signal,
          ),
          api.getProject(command.project_id, false),
        ]);
        if (operation.signal.aborted) return;
        const result = await performAgentControl(
          command,
          proof,
          project,
          callbacks.current.adapter,
          operation.signal,
        );
        if (operation.signal.aborted) return;
        submitted = true;
        const acknowledged = await api.reportAgentControlResult(
          command.project_id,
          command.run_id,
          result,
          operation.signal,
        );
        if (operation.signal.aborted) return;
        if (confirmedControlResult(command, result, acknowledged))
          await acknowledgeControlReceipt(command);
        else if (acknowledged.state.status === 'waiting_ui')
          throw new Error('Сервер ещё не подтвердил результат показа участка.');
        acceptCurrent(acknowledged);
      } catch (cause) {
        if (operation.signal.aborted) return;
        const message =
          cause instanceof Error
            ? cause.message
            : 'Не удалось подтвердить показ участка.';
        try {
          const current = await api.getAgentRun(
            command.project_id,
            command.run_id,
          );
          if (operation.signal.aborted) return;
          acceptCurrent(current);
          if (current.state.status !== 'waiting_ui') return;
        } catch {
          /* Keep the durable camera outcome; only a read/ACK can be retried. */
        }
        if (!operation.signal.aborted) {
          setError(message);
          setAcknowledgementPending(submitted);
        }
      }
    })();
    return () => {
      operation.abort();
      if (controller.current === operation) controller.current = undefined;
    };
  }, [identity, enabled, refresh]);

  return {
    error,
    acknowledgementPending,
    refresh: () => setRefresh((value) => value + 1),
    cancel: () => controller.current?.abort(),
  };
}
