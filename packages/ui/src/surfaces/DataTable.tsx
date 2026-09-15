import type { ComponentPropsWithRef, FC } from 'react';
import { tv } from 'tailwind-variants';
import { cx } from '../foundations/utils';

const dataTable = tv({
  base: 'w-full border-collapse text-sm [&_tbody>tr:not(:last-child)>td]:border-b [&_tbody>tr:not(:last-child)>td]:border-solid [&_tbody>tr:not(:last-child)>td]:border-neutral-200 [&_td]:border-0 [&_td]:px-3 [&_td]:py-2 [&_th]:border-0 [&_th]:px-3 [&_th]:py-2 [&_th]:text-xs [&_th]:font-medium [&_th]:text-neutral-500 [&_thead>tr>th]:border-b [&_thead>tr>th]:border-solid [&_thead>tr>th]:border-neutral-200 [:where(&)_th]:text-left',
  variants: {
    layout: { auto: 'table-auto', fixed: 'table-fixed' },
  },
});

export interface DataTableProps extends ComponentPropsWithRef<'table'> {
  /** Column sizes belong to the caller's native colgroup. */
  layout?: 'auto' | 'fixed';
  containerClassName?: string;
}

export const DataTable: FC<DataTableProps> = ({
  children,
  className,
  containerClassName,
  layout = 'auto',
  ...props
}) => (
  <div
    className={cx(
      'min-h-0 min-w-0 overflow-auto overscroll-contain',
      containerClassName,
    )}
  >
    <table {...props} className={dataTable({ layout, className })}>
      {children}
    </table>
  </div>
);
