import { Button, Disclosure, InlineMessage, Progress } from '@green/ui';
import { OperationProgress } from '@/entities/operation/ui/OperationProgress';
import { useCadIntake, type CadIntakeOptions } from '../model/useCadIntake';
import { CadPackageBrowser } from './CadPackageBrowser';
import { CadPassport } from './CadPassport';
import { useCadPreview } from '../model/useCadPreview';
import { CadWorkPreview } from './CadWorkPreview';
import { CadPreparedSource } from './CadPreparedSource';
import { useCadPreparation } from '../model/useCadPreparation';
import { startPrepare } from '../api/startPrepare';

export function CadIntakePanel(options: CadIntakeOptions) {
  const state = useCadIntake(options);
  const operation = state.operation;
  const preview = useCadPreview({
    projectId: options.projectId ?? operation?.project_id,
    version: options.projectStateVersion,
    onNavigate: options.onNavigate,
  });
  const prepared = useCadPreparation(
    {
      projectId: options.projectId ?? operation?.project_id,
      version: options.projectStateVersion,
      onNavigate: options.onNavigate,
    },
    'prepare_cad_project',
    startPrepare,
  );
  const passport = operation?.cad_intake?.passport;
  const historical =
    options.projectStateVersion != null &&
    operation?.project_state_version !== options.projectStateVersion;
  return (
    <div className="grid gap-4">
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
      {operation && (
        <OperationProgress
          operation={operation}
          title="Проверка CAD-комплекта"
          onCancel={state.cancel}
          actionBusy={state.cancelling}
        />
      )}
      {passport && <CadPassport passport={passport} historical={historical} />}
      <CadPreparedSource
        state={prepared}
        intake={operation}
        hasSource={options.hasSource}
        disabled={
          state.busy ||
          preview.busy ||
          historical ||
          operation?.status !== 'completed'
        }
      />
      {passport && !operation?.cad_intake?.request.overrides?.length && (
        <Button
          disabled={state.busy || preview.busy || prepared.busy}
          onClick={() =>
            state.launch({ rootId: passport.root_id, path: passport.entry })
          }
        >
          Повторить проверку комплекта
        </Button>
      )}
      {(passport || preview.operation || preview.error) && (
        <Disclosure
          title="Предварительный просмотр фрагмента (без редактирования)"
          defaultOpen={Boolean(preview.operation || preview.error)}
        >
          <CadWorkPreview
            intake={operation}
            state={preview}
            hasSource={options.hasSource}
            disabled={
              state.busy ||
              prepared.busy ||
              historical ||
              operation?.status !== 'completed'
            }
          />
        </Disclosure>
      )}
      {operation ? (
        <Disclosure
          title="Выбрать чертёж для новой проверки"
          defaultOpen={!passport && !state.busy}
        >
          <CadPackageBrowser
            busy={state.busy || preview.busy || prepared.busy}
            onStart={state.launch}
          />
        </Disclosure>
      ) : (
        <CadPackageBrowser
          busy={state.busy || state.loading || preview.busy || prepared.busy}
          onStart={state.launch}
        />
      )}
    </div>
  );
}
