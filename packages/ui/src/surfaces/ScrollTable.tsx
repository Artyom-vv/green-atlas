import type {
  ComponentPropsWithRef,
  CSSProperties,
  FC,
  ReactNode,
} from 'react';
import { cx } from '../foundations/utils';
import { ScrollArea } from './ScrollArea';
import { ScrollTableRow } from './ScrollTableParts';

export interface ScrollTableProps extends ComponentPropsWithRef<'div'> {
  /** Shared CSS grid tracks for the header and every body row. */
  columns: string;
  minWidth: CSSProperties['minWidth'];
  header: ReactNode;
  containerClassName?: string;
}

/** A bounded table with a stationary header and independently scrolling rows. */
export const ScrollTable: FC<ScrollTableProps> = ({
  children,
  columns,
  minWidth,
  header,
  className,
  containerClassName,
  style,
  ...props
}) => (
  <div
    className={cx(
      'min-h-0 min-w-0 overflow-x-auto overscroll-x-contain',
      containerClassName,
    )}
  >
    <div
      {...props}
      role="table"
      className={cx('flex h-full min-h-0 flex-col text-xs', className)}
      style={
        { '--table-columns': columns, minWidth, ...style } as CSSProperties
      }
    >
      <div
        role="rowgroup"
        data-slot="table-header"
        className="shrink-0 border-0 border-b border-solid border-neutral-200 bg-neutral-100"
      >
        <ScrollTableRow>{header}</ScrollTableRow>
      </div>
      <ScrollArea
        role="rowgroup"
        className="flex-1"
        viewportClassName="overscroll-x-auto"
      >
        {children}
      </ScrollArea>
    </div>
  </div>
);
