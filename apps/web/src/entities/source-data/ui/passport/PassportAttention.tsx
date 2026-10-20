import type { DataPassport, DataPassportEntry } from '@green/api-client';
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
    <section aria-label="Данные, требующие внимания" className="grid min-w-0">
      {entries.map((entry) => (
        <EntryDetails key={entry.kind} entry={entry} passport={passport} />
      ))}
    </section>
  );
}
