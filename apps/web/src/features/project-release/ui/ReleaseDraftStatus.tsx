import type { FC } from 'react';
import { Button, FormActions, InlineMessage } from '@green/ui';
import type { ReleaseDraftContext } from '../model/releaseDraft';

export interface ReleaseDraftStatusProps {
  draftStale?: boolean;
  draftContext?: Pick<ReleaseDraftContext, 'planVersion' | 'geometryVersion'>;
  planVersion: number;
  geometryVersion?: number;
  draftNotice?: string;
  submissionUnknown?: boolean;
  loading?: boolean;
  onReviewContext: () => void;
}

export const ReleaseDraftStatus: FC<ReleaseDraftStatusProps> = ({
  draftStale,
  draftContext,
  planVersion,
  geometryVersion,
  draftNotice,
  submissionUnknown,
  loading,
  onReviewContext,
}) => (
  <>
    {draftStale && (
      <InlineMessage tone="warning">
        <div className="grid gap-2">
          <p className="m-0">
            План или геометрия изменились. Заметки сохранены для проверки.
          </p>
          {draftContext && (
            <p className="m-0">
              Черновик: план {draftContext.planVersion}, геометрия{' '}
              {draftContext.geometryVersion}. Сейчас: план {planVersion},
              геометрия {geometryVersion}.
            </p>
          )}
          <FormActions className="justify-start">
            <Button
              variant="secondary"
              disabled={loading}
              onClick={onReviewContext}
            >
              Проверил основания для текущей версии
            </Button>
          </FormActions>
        </div>
      </InlineMessage>
    )}
    {submissionUnknown && (
      <InlineMessage tone="warning">
        Исход предыдущей сборки неизвестен. Заметки сохранены. Повторная сборка
        создаст новый пакет.
      </InlineMessage>
    )}
    {Boolean(draftNotice) && (
      <InlineMessage tone="warning">{draftNotice}</InlineMessage>
    )}
  </>
);
