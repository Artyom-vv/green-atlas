import { AssistantCard } from '@/features/assistant/ui/shared/AssistantCard';
import { Button, Text } from '@green/ui';
import type { FC } from 'react';
import type { RunFeedbackProps } from './RunFeedback.types';
export interface MapFeedbackProps extends Pick<
  RunFeedbackProps,
  'mapControl' | 'decisionDisabled'
> {}
export const MapFeedback: FC<MapFeedbackProps> = ({
  mapControl,
  decisionDisabled,
}) => (
  <>
    {!!mapControl.error && (
      <AssistantCard role="alert" tone="error">
        <Text as="p" variant="body" className="m-0 wrap-anywhere">
          {mapControl.error}
        </Text>
        {!!mapControl.acknowledgementPending && (
          <>
            <Text as="p" variant="body" className="m-0 wrap-anywhere">
              Движение карты не повторится. Проверим подтверждение результата.
            </Text>
            <Button
              type="button"
              variant="secondary"
              disabled={decisionDisabled}
              onClick={mapControl.refresh}
            >
              Обновить состояние показа
            </Button>
          </>
        )}
      </AssistantCard>
    )}
  </>
);
