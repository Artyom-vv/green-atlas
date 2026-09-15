import type { DataPassport, DataPassportEntry } from '@green/api-client';
import { DataTable, Text } from '@green/ui';
import { type FC } from 'react';
import { EntryDetails } from './EntryDetails';

interface PassportIncludedProps {
  entries: DataPassportEntry[];
  passport: DataPassport;
}
export const PassportIncluded: FC<PassportIncludedProps> = ({
  entries,
  passport,
}) => (
  <>
    {' '}
    {entries.length > 0 && (
      <section aria-label="Данные в расчёте">
        <Text as="h3" variant="label" className="mb-2">
          Учтено в расчёте
        </Text>
        <DataTable layout="fixed" className="min-w-112">
          <colgroup>
            <col className="w-[28%]" />
            <col className="w-28" />
            <col />
          </colgroup>
          <thead>
            <tr>
              <th scope="col">Данные</th>
              <th scope="col" className="text-right">
                Объектов
              </th>
              <th scope="col">
                <span className="sr-only">Подробности</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.kind}>
                <td className="align-top wrap-anywhere">{entry.label}</td>
                <td className="text-right align-top tabular-nums">
                  {entry.object_count.toLocaleString('ru-RU')}
                </td>
                <td className="align-top">
                  <EntryDetails entry={entry} passport={passport} />
                </td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      </section>
    )}{' '}
  </>
);
