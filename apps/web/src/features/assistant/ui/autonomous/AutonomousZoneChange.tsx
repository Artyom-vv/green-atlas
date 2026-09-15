import { zoneChangeSummary } from '@/entities/planting-zone/model/zoneChangeSummary';
import type { committedZoneChange } from '@/features/assistant/model/autonomous/committedZoneChange';
import { AssistantCard } from '@/features/assistant/ui/shared/AssistantCard';
import type { ZoneChangePreview } from '@green/api-client';
import { Text } from '@green/ui';
import type { FC } from 'react';
const area = (value: number | null) =>
  value === null
    ? '—'
    : `${value.toLocaleString('ru-RU', { maximumFractionDigits: 1 })} м²`;
export interface CommittedZoneChangeProps {
  change: NonNullable<ReturnType<typeof committedZoneChange>>;
}
export const CommittedZoneChange: FC<CommittedZoneChangeProps> = ({
  change,
}) => {
  return (
    <AssistantCard aria-label="Применённое изменение участка" tone="neutral">
      <span className="text-xs font-medium text-neutral-600">
        Результат этого запуска
      </span>
      <Text as="h3" variant="heading" className="m-0 wrap-anywhere">
        {change.title}
      </Text>
      <dl className="grid grid-cols-[repeat(auto-fit,minmax(112px,1fr))] gap-3 border-y border-neutral-200 py-3">
        <div>
          <dt className="text-xs text-neutral-500">До изменения</dt>
          <dd className="m-0 text-sm font-medium tabular-nums">
            {change.beforeLabel ?? 'Участка не было'}
          </dd>
          <dd className="m-0 text-sm font-medium tabular-nums">
            {area(change.beforeArea)}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-neutral-500">Применено</dt>
          <dd className="m-0 text-sm font-medium tabular-nums">
            {change.afterLabel ?? 'Участок удалён'}
          </dd>
          <dd className="m-0 text-sm font-medium tabular-nums">
            {area(change.afterArea)}
          </dd>
        </div>
      </dl>
      <Text as="p" variant="body" className="m-0 wrap-anywhere">
        Существующие посадки сохранены без изменений.
      </Text>
    </AssistantCard>
  );
};
export interface AutonomousZoneChangeProps {
  preview: ZoneChangePreview;
}
export const AutonomousZoneChange: FC<AutonomousZoneChangeProps> = ({
  preview,
}) => {
  const change = zoneChangeSummary(preview);
  if (!change) return null;
  return (
    <AssistantCard
      aria-label="Изменение участка"
      tone={preview.operation === 'delete' ? 'error' : 'neutral'}
    >
      <span className="text-xs font-medium text-neutral-600">
        {change.canApply
          ? 'Изменение подготовлено'
          : 'Изменение требует уточнения'}
      </span>
      <Text as="h3" variant="heading" className="m-0 wrap-anywhere">
        {change.title}
      </Text>
      <dl className="grid grid-cols-[repeat(auto-fit,minmax(112px,1fr))] gap-3 border-y border-neutral-200 py-3">
        <div>
          <dt className="text-xs text-neutral-500">Сейчас</dt>
          <dd className="m-0 text-sm font-medium tabular-nums">
            {change.beforeLabel ?? 'Участка нет'}
          </dd>
          <dd className="m-0 text-sm font-medium tabular-nums">
            {area(preview.before_area_m2)}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-neutral-500">После подтверждения</dt>
          <dd className="m-0 text-sm font-medium tabular-nums">
            {change.afterLabel ?? 'Участок будет удалён'}
          </dd>
          <dd className="m-0 text-sm font-medium tabular-nums">
            {area(preview.after_area_m2)}
          </dd>
        </div>
      </dl>
      {change.affected ? (
        <Text as="p" variant="body" className="m-0 wrap-anywhere">
          Изменение затрагивает посадки: {change.affected}. Требуется отдельное
          изменение плана.
        </Text>
      ) : (
        <Text as="p" variant="body" className="m-0 wrap-anywhere">
          Существующие посадки сохранятся без изменений.
        </Text>
      )}
      {preview.blockers.length > 0 && (
        <Text role="alert" as="p" variant="body" className="m-0 wrap-anywhere">
          {preview.blockers.join(' ')}
        </Text>
      )}
      <Text className="text-xs text-neutral-600" as="p" variant="body">
        {preview.operation === 'delete'
          ? 'Красный пунктир на карте — удаляемый контур.'
          : change.geometryChanged
            ? 'Пунктир — текущий контур. Синий — предложение.'
            : change.before
              ? 'На карте выделен участок. Его контур сохраняется.'
              : 'Синий контур на карте — новый участок.'}
      </Text>
    </AssistantCard>
  );
};
