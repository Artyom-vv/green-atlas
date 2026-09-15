import type { FC } from 'react';
import type {
  PlacementCheck,
  PlanObjectCreate,
  SpeciesRevision,
} from '@green/api-client';
import { Button, Field, FormActions, InlineMessage } from '@green/ui';
import { PlacementToolSurface } from '@/shared/ui/TaskSurface';
import { SpeciesPicker } from '@/entities/species';

export interface SinglePlacementPanelProps {
  kind: PlanObjectCreate['kind'];
  species: SpeciesRevision[];
  speciesId?: string;
  onSpeciesChange: (id: string) => void;
  check?: PlacementCheck;
  checking: boolean;
  placing: boolean;
  disabled?: boolean;
  error?: string;
  notice?: string;
  needsRefresh?: boolean;
  refreshing?: boolean;
  onRefresh?: () => void;
  onFinish: () => void;
}

export const SinglePlacementPanel: FC<SinglePlacementPanelProps> = ({
  kind,
  species,
  speciesId,
  onSpeciesChange,
  check,
  checking,
  placing,
  disabled,
  error,
  notice,
  needsRefresh,
  refreshing,
  onRefresh,
  onFinish,
}) => {
  const status = placing
    ? 'Проверяем выбранное место и сохраняем посадку…'
    : needsRefresh
      ? 'Обновите проект, чтобы продолжить размещение.'
      : !speciesId
        ? 'Выберите породу, затем кликните в нужном месте на карте.'
        : (notice ??
          (checking
            ? 'Проверяем место…'
            : (check?.reason ??
              'Кликните в нужном месте на карте. Перед посадкой проверим ограничения.')));
  return (
    <PlacementToolSurface
      title={kind === 'tree' ? 'Посадить дерево' : 'Посадить кустарник'}
      label="Одиночная посадка"
      footer={
        <FormActions layout="equal">
          <Button
            variant="secondary"
            disabled={disabled || placing || needsRefresh || refreshing}
            onClick={onFinish}
          >
            Завершить посадку
          </Button>
        </FormActions>
      }
    >
      <Field label="Порода" className="col-span-full">
        <SpeciesPicker
          species={species.filter((item) => item.kind === kind)}
          value={speciesId}
          onChange={onSpeciesChange}
          disabled={disabled || placing}
        />
      </Field>
      <p className="m-0 text-xs leading-4 text-neutral-600">
        Посадочный материал: стандартный.
      </p>
      <InlineMessage
        tone={
          check && !check.allowed && !checking && !placing ? 'warning' : 'info'
        }
      >
        {status}
      </InlineMessage>
      {check?.source_layer && !checking && !placing && (
        <p className="m-0 text-xs leading-4 text-neutral-600">
          Источник ограничения: {check.source_layer}
        </p>
      )}
      {error && <InlineMessage tone="error">{error}</InlineMessage>}
      {needsRefresh && onRefresh && (
        <FormActions layout="equal">
          <Button variant="secondary" loading={refreshing} onClick={onRefresh}>
            Обновить проект
          </Button>
        </FormActions>
      )}
    </PlacementToolSurface>
  );
};
