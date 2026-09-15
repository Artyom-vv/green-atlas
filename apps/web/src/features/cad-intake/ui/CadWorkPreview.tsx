import type { ProjectOperation } from '@green/api-client';
import { Button, InlineMessage, Progress, Text } from '@green/ui';
import { OperationProgress } from '@/entities/operation/ui/OperationProgress';
import type { useCadPreview } from '../model/useCadPreview';
import { CadBoundaryForm } from './CadBoundaryForm';

interface Props {
  intake?: ProjectOperation | null;
  state: ReturnType<typeof useCadPreview>;
  disabled: boolean;
  hasSource?: boolean;
}

export function CadWorkPreview({ intake, state, disabled, hasSource }: Props) {
  const passport = intake?.cad_intake?.passport;
  if (!passport && !state.operation && !state.error && !state.loading)
    return null;
  const completed =
    state.operation?.status === 'completed' &&
    state.operation.cad_preview?.result;
  return (
    <section
      className="grid min-w-0 gap-3 border-t border-neutral-200 pt-4"
      aria-label="Рабочая территория"
    >
      <Text as="h3" variant="heading">
        Рабочая территория
      </Text>
      {state.loading && <Progress label="Проверяем подготовку карты" />}
      {state.error && (
        <InlineMessage tone="error">
          <div className="flex flex-wrap items-center gap-3">
            <span>{state.error.message}</span>
            <Button onClick={() => void state.refresh()}>
              Обновить состояние карты
            </Button>
          </div>
        </InlineMessage>
      )}
      {state.operation && (
        <OperationProgress
          operation={state.operation}
          title="Подготовка карты"
          onCancel={state.cancel}
          actionBusy={state.cancelling}
        />
      )}
      {completed ? (
        <Button variant="primary" onClick={state.open} loading={state.opening}>
          Открыть предварительную карту
        </Button>
      ) : hasSource ? (
        <Text variant="caption">
          Для другого комплекта создайте новый проект.
        </Text>
      ) : (
        passport &&
        intake?.id && (
          <CadBoundaryForm
            key={`${intake.id}:${passport.manifest_sha256}`}
            passport={passport}
            intakeId={intake.id}
            disabled={disabled || state.busy || state.loading}
            onStart={state.launch}
          />
        )
      )}
    </section>
  );
}
