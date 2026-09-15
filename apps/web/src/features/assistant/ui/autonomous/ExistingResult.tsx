import type { AutonomousRunController } from '@/features/assistant/model/autonomous/useAutonomousRunController';
import { AssistantCard } from '@/features/assistant/ui/shared/AssistantCard';
import { Text } from '@green/ui';
import type { FC } from 'react';
export interface ExistingResultProps extends Pick<
  AutonomousRunController,
  | 'existing'
  | 'reviewStatus'
  | 'committed'
  | 'reviewNotice'
  | 'existingScope'
  | 'existingSpecies'
  | 'previousSpecies'
> {}
export const ExistingResult: FC<ExistingResultProps> = ({
  existing,
  reviewStatus,
  committed,
  reviewNotice,
  existingScope,
  existingSpecies,
  previousSpecies,
}) => (
  <>
    {!!existing && (
      <AssistantCard
        aria-label="Изменение посадок"
        tone={
          reviewStatus
            ? 'muted'
            : existing.action === 'delete'
              ? 'error'
              : 'neutral'
        }
      >
        <span className="text-xs font-medium text-neutral-600">
          {reviewStatus ??
            (committed ? 'Изменение применено' : 'Изменение подготовлено')}
        </span>
        <Text as="h3" variant="heading" className="m-0 wrap-anywhere">
          {existing.title}
        </Text>
        {!!reviewNotice && (
          <Text className="text-xs text-neutral-600" as="p" variant="body">
            {reviewNotice}
          </Text>
        )}
        <Text as="p" variant="body" className="m-0 wrap-anywhere">
          <Text as="strong" variant="label" className="m-0 wrap-anywhere">
            Участок:
          </Text>{' '}
          {existingScope?.length
            ? existingScope.join(', ')
            : 'Участок не назначен'}
          {existing.unassignedZones && existingScope?.length
            ? ` · Без участка: ${existing.unassignedZones}`
            : ''}
        </Text>
        <Text as="p" variant="body" className="m-0 wrap-anywhere">
          {[
            existing.kinds.tree ? `Деревья: ${existing.kinds.tree}` : '',
            existing.kinds.shrub ? `Кустарники: ${existing.kinds.shrub}` : '',
          ]
            .filter(Boolean)
            .join(' · ')}
        </Text>
        {!!existingSpecies?.length && (
          <Text as="p" variant="body" className="m-0 wrap-anywhere">
            <Text as="strong" variant="label" className="m-0 wrap-anywhere">
              {existing.action === 'species' ? 'Новая порода:' : 'Порода:'}
            </Text>{' '}
            {existingSpecies.join(', ')}
          </Text>
        )}
        {!!(existing.action === 'species' && previousSpecies?.length) && (
          <Text as="p" variant="body" className="m-0 wrap-anywhere">
            <Text as="strong" variant="label" className="m-0 wrap-anywhere">
              Сейчас:
            </Text>{' '}
            {previousSpecies.join(', ')}
          </Text>
        )}
        {!!existing.move && (
          <Text as="p" variant="body" className="m-0 wrap-anywhere">
            <Text as="strong" variant="label" className="m-0 wrap-anywhere">
              Сдвиг:
            </Text>{' '}
            X {existing.move.dx > 0 ? '+' : ''}
            {existing.move.dx.toLocaleString('ru-RU', {
              maximumFractionDigits: 3,
            })}{' '}
            м · Y {existing.move.dy > 0 ? '+' : ''}
            {existing.move.dy.toLocaleString('ru-RU', {
              maximumFractionDigits: 3,
            })}{' '}
            м
          </Text>
        )}
        {(existing.action === 'lock' || existing.action === 'unlock') && (
          <Text as="p" variant="body" className="m-0 wrap-anywhere">
            Закрепление: {existing.action === 'lock' ? 'включить' : 'снять'}.
          </Text>
        )}
        {existing.action === 'delete' && !committed && !reviewStatus && (
          <Text className="text-xs text-neutral-600" as="p" variant="body">
            Посадки будут удалены из плана после подтверждения.
          </Text>
        )}
      </AssistantCard>
    )}
  </>
);
