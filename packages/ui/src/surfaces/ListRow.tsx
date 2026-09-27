import type { FC, ReactNode } from 'react';
import { cx } from '../foundations/utils';
export interface ListRowProps {
  selected?: boolean;
  leading?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  onClick?: () => void;
  className?: string;
}
export const ListRow: FC<ListRowProps> = ({
  selected = false,
  leading,
  title,
  description,
  meta,
  actions,
  onClick,
  className,
}) => (
  <div
    className={cx(
      'flex min-h-12 min-w-0 items-center gap-2.5 border-b border-(--border) px-3 py-2 last:border-b-0',
      selected && 'border-l-2 border-l-(--selection) bg-(--selection-soft)',
      onClick && 'cursor-pointer hover:bg-(--canvas)',
      className,
    )}
    onClick={onClick}
    onKeyDown={
      onClick
        ? (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              onClick();
            }
          }
        : undefined
    }
    role={onClick ? 'button' : undefined}
    tabIndex={onClick ? 0 : undefined}
  >
    {leading}
    <div className="grid min-w-0 flex-1 gap-px">
      <strong className="text-[13px] leading-4 font-normal">{title}</strong>
      {!!description && (
        <span className="truncate font-mono text-[10px] leading-3 text-neutral-500">
          {description}
        </span>
      )}
    </div>
    {!!meta && (
      <div className="font-mono text-[11px] text-neutral-500">{meta}</div>
    )}
    {actions}
  </div>
);
