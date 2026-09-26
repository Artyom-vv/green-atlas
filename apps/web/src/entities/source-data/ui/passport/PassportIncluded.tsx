import type { DataPassport, DataPassportEntry } from '@green/api-client';
import { Text } from '@green/ui';
import { EntryDetails } from './EntryDetails';

export function PassportIncluded({
  entries,
  passport,
}: {
  entries: DataPassportEntry[];
  passport: DataPassport;
}) {
  if (!entries.length) return null;
  return (
    <section aria-label="Слои подготовленной карты" className="grid gap-2">
      <Text as="h3" variant="label">
        Слои подготовленной карты
      </Text>
      {entries.map((entry) => (
        <EntryDetails key={entry.kind} entry={entry} passport={passport} />
      ))}
    </section>
  );
}
