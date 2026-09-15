import { repeatedItemLabel } from '@/entities/planting-zone/model/plantingZoneLabels';
import {
  readSummary,
  SpeciesOption,
} from '@/features/assistant/model/autonomous/presentation';
import { type Project } from '@green/api-client';
import { Text } from '@green/ui';
import type { FC } from 'react';
export interface ReadEvidenceProps {
  read: ReturnType<typeof readSummary>;
  project?: Project;
}
export const ReadEvidence: FC<ReadEvidenceProps> = ({ read, project }) => {
  const zones = read.zoneIds
    .map((id) => project?.planting_zones?.find((zone) => zone.id === id))
    .map((zone) =>
      zone
        ? repeatedItemLabel(project?.planting_zones ?? [], zone)
        : 'Название участка недоступно',
    );
  return (
    <>
      {zones.length > 0 && (
        <Text as="p" variant="body" className="m-0 wrap-anywhere">
          <Text as="strong" variant="label" className="m-0 wrap-anywhere">
            {zones.length === 1 ? 'Участок:' : 'Участки:'}
          </Text>{' '}
          {zones.join(', ')}
        </Text>
      )}
      {!!read.source && (
        <Text as="p" variant="body" className="m-0 wrap-anywhere">
          <Text as="strong" variant="label" className="m-0 wrap-anywhere">
            Источник:
          </Text>{' '}
          {read.source}
        </Text>
      )}
      {!!(read.pages && read.pages > 1) && (
        <Text as="p" variant="body" className="m-0 wrap-anywhere">
          Прочитано страниц: {read.pages}.
        </Text>
      )}
    </>
  );
};
export interface SpeciesOptionRowProps {
  item: SpeciesOption;
}
export const SpeciesOptionRow: FC<SpeciesOptionRowProps> = ({ item }) => {
  return (
    <li className="grid min-w-0 gap-1 border-t border-neutral-200 pt-2">
      <Text as="strong" variant="label" className="m-0 wrap-anywhere">
        {item.name}
      </Text>
      {!!item.scientificName && (
        <Text as="small" variant="caption" className="m-0 wrap-anywhere">
          {item.scientificName}
        </Text>
      )}
      {(item.status === 'available' || item.status === 'review') && (
        <span>
          {item.status === 'review'
            ? 'Нужна дополнительная проверка'
            : 'Доступна в подборе'}
        </span>
      )}
      {item.reasons.length > 0 && (
        <Text as="p" variant="body" className="m-0 wrap-anywhere">
          {item.reasons.join(' ')}
        </Text>
      )}
    </li>
  );
};
