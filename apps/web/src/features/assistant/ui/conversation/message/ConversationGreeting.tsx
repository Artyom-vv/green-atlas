import { Button, Text } from '@green/ui';
import type { FC } from 'react';
import type { ConversationHistoryProps } from '../ConversationHistory.types';

interface ConversationGreetingProps {
  setDraft: ConversationHistoryProps['chat']['setDraft'];
  composer: ConversationHistoryProps['composer'];
  disabled: boolean;
}
export const ConversationGreeting: FC<ConversationGreetingProps> = ({
  setDraft,
  composer,
  disabled,
}) => (
  <div className="grid gap-3 py-4">
    <Text as="h3" variant="heading" className="m-0 wrap-anywhere">
      Что изменим в проекте?
    </Text>
    <Text as="p" variant="body" className="m-0 wrap-anywhere">
      Опишите задачу. Сначала покажу предложение на карте.
    </Text>
    <div className="flex flex-wrap gap-2">
      <Button
        variant="secondary"
        disabled={disabled}
        onClick={() => {
          setDraft('Посади группы деревьев вдоль зданий');
          composer.current?.focus();
        }}
      >
        Посадить вдоль зданий
      </Button>
      <Button
        variant="secondary"
        disabled={disabled}
        onClick={() => {
          setDraft('Покажи рост через 20 лет');
          composer.current?.focus();
        }}
      >
        Посмотреть рост
      </Button>
    </div>
  </div>
);
