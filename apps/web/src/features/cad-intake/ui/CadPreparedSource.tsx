import type { ProjectOperation } from '@green/api-client';
import { Button, InlineMessage, Text } from '@green/ui';
import { OperationProgress } from '@/entities/operation/ui/OperationProgress';
import type { CadPrepareRequest } from '@green/api-client';
import type { useCadPreparation } from '../model/useCadPreparation';

interface Props {
  state: ReturnType<typeof useCadPreparation<CadPrepareRequest>>;
  intake?: ProjectOperation | null;
  disabled: boolean;
  hasSource?: boolean;
}

export function CadPreparedSource({
  intake,
  disabled,
  hasSource,
  state,
}: Props) {
  const passport = intake?.cad_intake?.passport;
  const intakeId = intake?.id;
  const entry = passport?.drawings.find((item) => item.path === passport.entry);
  const eligible =
    passport?.entry.toLowerCase().endsWith('.dxf') &&
    entry?.status === 'readable' &&
    entry.inspection &&
    !Object.keys(entry.inspection.xrefs).length &&
    !passport.references.length;
  if (!eligible && !state.operation && !state.error) return null;
  const completed = state.operation?.status === 'completed';
  return (
    <section className="grid min-w-0 gap-3" aria-label="Полный исходник">
      <Text as="h3" variant="heading">
        Полный исходник
      </Text>
      <Text variant="caption">
        Все объекты DXF будут сохранены. Проверка слоёв и ограничений доступна
        после открытия редактора.
      </Text>
      {state.error && (
        <InlineMessage tone="error">{state.error.message}</InlineMessage>
      )}
      {state.operation && (
        <OperationProgress
          operation={state.operation}
          title="Подготовка полного DXF"
          onCancel={state.cancel}
          actionBusy={state.cancelling}
        />
      )}
      {completed ? (
        <Button variant="primary" onClick={state.open} loading={state.opening}>
          Открыть редактор
        </Button>
      ) : (
        !hasSource &&
        eligible &&
        intakeId &&
        passport && (
          <Button
            variant="primary"
            disabled={disabled || state.busy || state.loading}
            onClick={() =>
              state.launch({
                intake_operation_id: intakeId,
                manifest_sha256: passport.manifest_sha256,
                profile_version: 1,
              })
            }
          >
            Подготовить полный DXF и редактировать
          </Button>
        )
      )}
    </section>
  );
}
