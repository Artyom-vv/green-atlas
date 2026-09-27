import type { DataPassport, DataPassportEntry } from '@green/api-client';
import { Text } from '@green/ui';
import { EntryDetails } from './EntryDetails';

export function PassportAttention({
  entries,
  passport,
}: {
  entries: DataPassportEntry[];
  passport: DataPassport;
}) {
  if (!entries.length) return null;
  return (
    <section aria-label="Данные, требующие внимания" className="grid gap-2">
      <Text as="h3" variant="label">
        Что требует уточнения
      </Text>
      {entries.map((entry) => (
        <EntryDetails key={entry.kind} entry={entry} passport={passport} />
      ))}
    </section>
  );
}
