import { capacityText } from '@/features/assistant/model/autonomous/presentation';
import { Text } from '@green/ui';
import type { FC } from 'react';
import type { PlacementResultProps } from './PlacementResult.types';
export interface PlacementSummaryProps extends Omit<
  Pick<PlacementResultProps, 'reviewStatus' | 'result' | 'reviewNotice'>,
  'result'
> {
  result: NonNullable<PlacementResultProps['result']>;
}
export const PlacementSummary: FC<PlacementSummaryProps> = ({
  reviewStatus,
  result,
  reviewNotice,
}) => (
  <>
    <span className="text-xs font-medium text-neutral-600">
      {reviewStatus ??
        (result.status === 'exact'
          ? 'Количество подобрано'
          : result.status === 'partial'
            ? 'Найден частичный вариант'
            : 'Вариант не найден')}
    </span>
    <Text as="h3" variant="heading" className="m-0 wrap-anywhere">
      {capacityText(result)}
    </Text>
    {!!reviewNotice && (
      <Text className="text-xs text-neutral-600" as="p" variant="body">
        {reviewNotice}
      </Text>
    )}
    {result.status !== 'exact' && (
      <dl className="grid grid-cols-[repeat(auto-fit,minmax(72px,1fr))] gap-3 border-y border-neutral-200 py-3">
        <div>
          <dt className="text-xs text-neutral-500">Найдено</dt>
          <dd className="m-0 text-sm font-medium tabular-nums">
            {result.found}
          </dd>
        </div>
        {result.requested !== undefined && (
          <div>
            <dt className="text-xs text-neutral-500">В задании</dt>
            <dd className="m-0 text-sm font-medium tabular-nums">
              {result.requested}
            </dd>
          </div>
        )}
        {result.shortfall > 0 && (
          <div>
            <dt className="text-xs text-neutral-500">Не хватает</dt>
            <dd className="m-0 text-sm font-medium tabular-nums">
              {result.shortfall}
            </dd>
          </div>
        )}
      </dl>
    )}
  </>
);
