import { repeatedItemLabel } from '@/entities/planting-zone/model/plantingZoneLabels';
import { AssistantCard } from '@/features/assistant/ui/shared/AssistantCard';
import { Button, Field, Select } from '@green/ui';
import type { FC } from 'react';
import { useState } from 'react';
import type { ConversationHistoryProps } from '../ConversationHistory.types';

interface ConversationZoneQuestionProps {
  chat: Pick<
    ConversationHistoryProps['chat'],
    'project' | 'chooseZone' | 'pending'
  >;
}
export const ConversationZoneQuestion: FC<ConversationZoneQuestionProps> = ({
  chat,
}) => {
  const [zoneId, setZoneId] = useState('');
  const busy = Boolean(chat.pending);
  return (
    <AssistantCard tone="neutral">
      <Field label="На каком участке?">
        <Select
          aria-label="Участок для предложения"
          value={zoneId}
          onChange={(event) => setZoneId(event.target.value)}
        >
          <option value="">Выберите участок</option>
          {chat.project?.planting_zones?.map((zone) => (
            <option key={zone.id} value={zone.id}>
              {repeatedItemLabel(
                chat.project?.planting_zones ?? [],
                zone,
              ).replace(/\s*[·•]\s*/g, ' ')}
            </option>
          ))}
          <option value="__project__">Весь проект</option>
        </Select>
      </Field>
      <Button
        variant="primary"
        disabled={!zoneId || busy}
        onClick={() => chat.chooseZone(zoneId)}
      >
        Показать предложение
      </Button>
    </AssistantCard>
  );
};
