import { Text } from '@green/ui';
import type { FC } from 'react';
import type { PlacementResultProps } from './PlacementResult.types';
export interface PlacementExplanationProps extends Omit<
  Pick<
    PlacementResultProps,
    | 'result'
    | 'incomplete'
    | 'reviewStatus'
    | 'run'
    | 'committed'
    | 'approval'
    | 'recoveryPending'
    | 'outcomeUnknown'
  >,
  'result'
> {
  result: NonNullable<PlacementResultProps['result']>;
}
export const PlacementExplanation: FC<PlacementExplanationProps> = ({
  result,
  incomplete,
  reviewStatus,
  run,
  committed,
  approval,
  recoveryPending,
  outcomeUnknown,
}) => (
  <>
    {!!(result.reason || incomplete) && (
      <div className="grid gap-1">
        {result.reason ? (
          <Text as="p" variant="body" className="m-0 wrap-anywhere">
            {result.reason}
          </Text>
        ) : (
          <Text as="p" variant="body" className="m-0 wrap-anywhere">
            В проверенном варианте не удалось выполнить задание целиком. Можно
            изменить условия и повторить расчёт.
          </Text>
        )}
        {!!incomplete && (
          <Text className="text-xs text-neutral-600" as="p" variant="body">
            Это результат проверенных вариантов, а не предел вместимости
            участка. План не изменён.
          </Text>
        )}
      </div>
    )}
    {!incomplete &&
      !reviewStatus &&
      (run?.state.status === 'cancelled' ? (
        <Text className="text-xs text-neutral-600" as="p" variant="body">
          Предложение отклонено. План не изменён.
        </Text>
      ) : !committed && !approval && !recoveryPending && !outcomeUnknown ? (
        <Text className="text-xs text-neutral-600" as="p" variant="body">
          Места подобраны. План изменится после вашего подтверждения.
        </Text>
      ) : null)}
  </>
);
