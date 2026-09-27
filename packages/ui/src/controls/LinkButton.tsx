import { useRender } from '@base-ui/react/use-render';
import type { ComponentPropsWithRef, FC, ReactElement, ReactNode } from 'react';
import { useControlSize } from '../foundations/ControlProvider';
import { Icon, type IconNode } from '../foundations/Icon';
import type { ControlSize } from '../foundations/utils';
import { buttonVariants, type ButtonVariant } from './buttonVariants';

export interface LinkButtonProps extends Omit<
  ComponentPropsWithRef<'a'>,
  'content'
> {
  render?: ReactElement;
  variant?: ButtonVariant;
  controlSize?: ControlSize;
  icon?: IconNode;
  startIcon?: ReactNode;
  endIcon?: ReactNode;
  content?: ReactNode;
}

export const LinkButton: FC<LinkButtonProps> = ({
  render,
  ref,
  variant = 'secondary',
  controlSize,
  icon,
  startIcon,
  endIcon,
  children,
  content,
  className,
  ...props
}) => {
  const size = useControlSize(controlSize);
  const styles = buttonVariants({ variant, size });
  return useRender({
    defaultTagName: 'a',
    render,
    ref,
    props: {
      ...props,
      'data-slot': 'link-button',
      'data-size': size,
      'data-variant': variant,
      className: styles.root({ className: ['no-underline', className] }),
      children: (
        <>
          <Icon icon={startIcon ?? icon} />
          {content === undefined ? (
            <span className={styles.label()}>{children}</span>
          ) : (
            content
          )}
          <Icon icon={endIcon} />
        </>
      ),
    },
  });
};
