import type { FC } from 'react';
import type { BrushStroke } from '@green/api-client';
import { InlineMessage } from '@green/ui';

interface BrushGuideProps {
  hasZones: boolean;
  needsSpecies: boolean;
  operation: BrushStroke['mode'];
  hasStrokes: boolean;
  hasPreview: boolean;
  loading?: boolean;
}

export const BrushGuide: FC<BrushGuideProps> = ({
  hasZones,
  needsSpecies,
  operation,
  hasStrokes,
  hasPreview,
  loading,
}) => {
  let message;
  if (!hasZones) message = <strong>Выберите участок</strong>;
  else if (needsSpecies)
    message =
      operation === 'add'
        ? 'Выберите породу перед рисованием. Она нужна для прогноза роста.'
        : 'Выберите породу для добавляющих мазков. Она нужна для прогноза роста.';
  else if (!hasStrokes) message = 'Рисуйте по участку';
  else if (!hasPreview || loading)
    message = 'Контуры — ещё не проверенные места';
  return message && <InlineMessage tone="info">{message}</InlineMessage>;
};
