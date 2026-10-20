import { Button as BaseButton } from '@base-ui/react/button';
import { ChevronRight } from 'lucide-react';
import type { ComponentPropsWithRef, FC, ReactNode } from 'react';
import { Icon } from '../foundations/Icon';
import { cx } from '../foundations/utils';
import { sectionRow } from './sectionRowVariants';

export interface ActionRowProps extends ComponentPropsWithRef<'button'> {
  endIcon?: ReactNode;
  /** A containing group owns whether this row needs a bottom separator. */
  separator?: boolean;
}

/** Opens a task from a full-width list without borrowing a control's density. */
export const ActionRow: FC<ActionRowProps> = ({
  children,
  endIcon = <ChevronRight />,
  separator = true,
  className,
  type = 'button',
  ...props
}) => (
  <BaseButton
    {...props}
    type={type}
    data-slot="action-row"
    className={sectionRow({
      className: cx(
        separator && 'border-b border-solid border-neutral-200',
        className,
      ),
    })}
  >
    <span className="min-w-0 flex-1 wrap-anywhere">{children}</span>
    <Icon icon={endIcon} />
  </BaseButton>
);
