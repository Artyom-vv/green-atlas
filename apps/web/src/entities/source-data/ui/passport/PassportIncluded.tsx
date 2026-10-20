import type { DataPassport, DataPassportEntry } from '@green/api-client';
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
    <section aria-label="Слои подготовленной карты" className="grid min-w-0">
      {entries.map((entry) => (
        <EntryDetails key={entry.kind} entry={entry} passport={passport} />
      ))}
    </section>
  );
}
