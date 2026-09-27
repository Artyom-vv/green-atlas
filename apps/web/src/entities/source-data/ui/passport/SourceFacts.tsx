import { type FC, type ReactNode } from 'react';
interface SourceFactsProps {
  items: { label: string; value: ReactNode }[];
}
export const SourceFacts: FC<SourceFactsProps> = ({ items }) => (
  <dl className="m-0 grid grid-cols-[repeat(auto-fit,minmax(min(12rem,100%),1fr))] gap-x-6 gap-y-3 text-xs">
    {items.map(({ label, value }) => (
      <div key={label}>
        <dt className="mb-1 text-neutral-600">{label}</dt>
        <dd className="m-0 wrap-anywhere">{value}</dd>
      </div>
    ))}
  </dl>
);
