import { useRef, useState } from 'react';
import { Button, Disclosure, InlineMessage, Progress } from '@green/ui';
import { OperationProgress } from '@/entities/operation/ui/OperationProgress';
import { useCadIntake, type CadIntakeOptions } from '../model/useCadIntake';
import { CadPackageBrowser } from './CadPackageBrowser';
import { CadPassport } from './CadPassport';
import { useCadPreparation } from '../model/useCadPreparation';
import { startPrepare } from '../api/startPrepare';
import { CadOpeningWizard } from './CadOpeningWizard';

export function CadIntakePanel(options: CadIntakeOptions) {
  const state = useCadIntake(options);
  const operation = state.operation;
  const [closedPassport, setClosedPassport] = useState<string>();
  const [replace, setReplace] = useState(false);
  const projectId = options.projectId ?? operation?.project_id;
  const destination = useRef<'setup' | 'workspace'>('workspace');
  const prepared = useCadPreparation(
    {
      projectId,
      version: options.projectStateVersion,
      onNavigate: () =>
        options.onNavigate(`/projects/${projectId}/${destination.current}`),
      autoOpen: false,
    },
    'prepare_cad_project',
    startPrepare,
  );
  const passport = operation?.cad_intake?.passport;
  const historical =
    options.projectStateVersion != null &&
    operation?.project_state_version !== options.projectStateVersion;
  const busy = state.busy || prepared.busy;
  const retry = () => {
    const request = operation?.cad_intake?.request;
    if (!request) return;
    state.launch({
      rootId: request.root_id,
      path: request.entry,
      additionalEntries: request.additional_entries,
    });
    setClosedPassport(undefined);
  };
  return (
    <div className="grid min-w-0 gap-4">
      {state.loading && <Progress label="Проверяем текущую обработку" />}
      {state.error && (
        <InlineMessage tone="error">
          <div className="flex flex-wrap items-center gap-3">
            <span>{state.error.message}</span>
            {state.canRefresh && (
              <Button onClick={() => void state.refresh()}>
                Обновить состояние
              </Button>
            )}
          </div>
        </InlineMessage>
      )}
      {operation && state.busy && (
        <OperationProgress
          operation={operation}
          title="Чтение файлов и проверка комплекта"
          onCancel={state.cancel}
          actionBusy={state.cancelling}
        />
      )}
      {operation && !state.busy && !passport && (
        <OperationProgress
          operation={operation}
          title="Проверка комплекта"
          onRetry={retry}
        />
      )}
      {prepared.operation &&
        prepared.busy &&
        closedPassport === operation?.id && (
          <OperationProgress
            operation={prepared.operation}
            title="Подготовка проекта"
            onCancel={prepared.cancel}
            actionBusy={prepared.cancelling}
          />
        )}
      {passport && operation && (
        <>
          <div className="grid gap-3">
            <p className="m-0 text-sm">
              {prepared.busy
                ? 'Подготавливаем доступные данные. Решения по комплекту сохранены.'
                : historical
                  ? 'Сохранённая проверка комплекта'
                  : 'Проверка завершена. Выберите, как открыть проект.'}
            </p>
            <div className="flex flex-wrap gap-3">
              {!options.hasSource && (
                <Button
                  variant="primary"
                  disabled={state.busy || historical}
                  onClick={() => {
                    setReplace(false);
                    setClosedPassport(undefined);
                  }}
                >
                  Продолжить открытие
                </Button>
              )}
              <Button disabled={busy || options.hasSource} onClick={retry}>
                Перепроверить
              </Button>
              {options.hasSource && (
                <Button
                  onClick={() =>
                    options.onNavigate(`/projects/${projectId}/workspace`)
                  }
                >
                  Вернуться к плану
                </Button>
              )}
            </div>
          </div>
          <Disclosure title="Замечания и состав комплекта">
            <CadPassport passport={passport} historical={historical} />
          </Disclosure>
          <CadOpeningWizard
            key={operation.id}
            intake={operation}
            prepared={{
              ...prepared,
              open: () => {
                destination.current = 'workspace';
                prepared.open();
              },
            }}
            open={
              !options.hasSource &&
              !historical &&
              !state.busy &&
              !replace &&
              closedPassport !== operation.id
            }
            onClose={() => setClosedPassport(operation.id)}
            onReplace={() => {
              setReplace(true);
              setClosedPassport(operation.id);
            }}
            onRetry={retry}
            disabled={state.busy || historical}
            onMapping={() => {
              destination.current = 'setup';
              prepared.open();
            }}
          />
        </>
      )}
      {(!operation || replace || (!passport && !state.busy)) && (
        <CadPackageBrowser
          busy={busy}
          onStart={(selection) => {
            state.launch(selection);
            setReplace(false);
            setClosedPassport(undefined);
          }}
        />
      )}
    </div>
  );
}
