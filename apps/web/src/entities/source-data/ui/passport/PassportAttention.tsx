import type { DataPassport, DataPassportEntry } from '@green/api-client';
import { Text } from '@green/ui';
import { type FC } from 'react';
import { EntryDetails } from './EntryDetails';
const STATUS_LABELS: Record<DataPassportEntry['status'], string> = {
  verified: 'Распознано',
  partial: 'Неполные данные',
  missing: 'Нет данных',
  excluded: 'Исключено',
};
interface PassportAttentionProps {
  entries: DataPassportEntry[];
  passport: DataPassport;
}
export const PassportAttention: FC<PassportAttentionProps> = ({
  entries,
  passport,
}) => (
  <>
    {' '}
    {entries.length > 0 && (
      <section aria-label="Данные, требующие внимания">
        <Text as="h3" variant="label" className="mb-2">
          Что требует уточнения
        </Text>
        <ul className="m-0 grid list-none p-0">
          {entries.map((entry) => (
            <li
              className="border-b border-neutral-200 py-3 last:border-0 last:pb-0"
              key={entry.kind}
            >
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                <Text as="strong" variant="label">
                  {entry.label}
                </Text>
                <Text variant="caption">{STATUS_LABELS[entry.status]}</Text>
              </div>
              <EntryDetails entry={entry} passport={passport} />
            </li>
          ))}
        </ul>
      </section>
    )}{' '}
  </>
);
