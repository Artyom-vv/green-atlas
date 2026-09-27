import type { FC, ReactNode } from 'react';
import { cx } from '../foundations/utils';

export interface SectionProps {
  title?: ReactNode;
  children: ReactNode;
  className?: string;
}

export const Section: FC<SectionProps> = ({ title, children, className }) => (
  <section
    className={cx(
      'min-w-0 border-t border-(--border) pt-5 first:border-t-0 first:pt-0',
      className,
    )}
  >
    {!!title && (
      <h3 className="mt-0 mb-3 text-[13px] leading-4 font-semibold">{title}</h3>
    )}
    {children}
  </section>
);
