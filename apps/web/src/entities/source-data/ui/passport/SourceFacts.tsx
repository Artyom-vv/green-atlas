import { Fragment, type FC, type ReactNode } from 'react';
interface SourceFactsProps {
  items: { label: string; value: ReactNode }[];
}
export const SourceFacts: FC<SourceFactsProps> = ({ items }) => (
  <dl className="m-0 grid grid-cols-[minmax(0,1fr)_minmax(0,2fr)] items-baseline gap-x-4 gap-y-2 text-sm leading-5">
    {items.map(({ label, value }) => (
      <Fragment key={label}>
        <dt className="text-sm leading-5 text-neutral-500">{label}</dt>
        <dd className="m-0 wrap-anywhere">{value}</dd>
      </Fragment>
    ))}
  </dl>
);
