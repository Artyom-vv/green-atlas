import { remedyDraft } from '@/features/assistant/model/autonomous/presentation';
import { Button, Text } from '@green/ui';
import type { FC } from 'react';
import type { PlacementResultProps } from './PlacementResult.types';
export interface PlacementRemediesProps extends Omit<
  Pick<
    PlacementResultProps,
    'incomplete' | 'result' | 'inputDisabled' | 'fillDraft' | 'answering'
  >,
  'result'
> {
  result: NonNullable<PlacementResultProps['result']>;
}
export const PlacementRemedies: FC<PlacementRemediesProps> = ({
  incomplete,
  result,
  inputDisabled,
  fillDraft,
  answering,
}) => (
  <>
    {!!(incomplete && result.remedies.length) && (
      <div className="flex flex-wrap items-center gap-2 border-t border-neutral-200 pt-3">
        <Text as="strong" variant="label" className="m-0 wrap-anywhere">
          Как продолжить
        </Text>
        {result.remedies
          .filter(
            (remedy) => typeof remedy.label === 'string' && remedyDraft(remedy),
          )
          .map((remedy) => (
            <Button
              type="button"
              variant="secondary"
              key={String(remedy.code)}
              disabled={inputDisabled}
              onClick={() => fillDraft(remedyDraft(remedy))}
            >
              {String(remedy.label)}
            </Button>
          ))}
        <Text as="small" variant="caption" className="m-0 wrap-anywhere">
          Выберите вариант, при необходимости дополните ответ и нажмите «
          {answering ? 'Продолжить' : 'Поставить задачу'}».
        </Text>
      </div>
    )}
  </>
);
