import type { ComponentPropsWithRef, FC } from 'react';
import { cx } from '../foundations/utils';

interface ScrollTablePartProps extends ComponentPropsWithRef<'div'> {}

export const ScrollTableRow: FC<ScrollTablePartProps> = ({
  className,
  ...props
}) => (
  <div
    {...props}
    role="row"
    className={cx(
      'grid grid-cols-(--table-columns) items-center border-0 not-last:border-b not-last:border-solid not-last:border-neutral-200 data-[selected=true]:bg-blue-100',
      className,
    )}
  />
);

export const ScrollTableCell: FC<ScrollTablePartProps> = ({
  className,
  ...props
}) => (
  <div
    {...props}
    role="cell"
    className={cx('min-w-0 px-3 py-2 wrap-anywhere', className)}
  />
);

export const ScrollTableColumnHeader: FC<ScrollTablePartProps> = ({
  className,
  ...props
}) => (
  <div
    {...props}
    role="columnheader"
    className={cx('min-w-0 px-3 py-2 font-medium text-neutral-500', className)}
  />
);
