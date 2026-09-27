import { Button as BaseButton } from '@base-ui/react/button';
import { LoaderCircle } from 'lucide-react';
import type { ComponentPropsWithRef, FC, ReactNode } from 'react';
import { useControlSize } from '../foundations/ControlProvider';
import { Icon, type IconNode } from '../foundations/Icon';
import { type ControlSize } from '../foundations/utils';
import { buttonVariants, type ButtonVariant } from './buttonVariants';
export interface ButtonProps extends Omit<
  ComponentPropsWithRef<'button'>,
  'content'
> {
  variant?: ButtonVariant;
  loading?: boolean;
  /** Legacy constructor adapter. New composition uses startIcon/endIcon nodes. */
  icon?: IconNode;
  startIcon?: ReactNode;
  endIcon?: ReactNode;
  /** Replaces the standard single-line label scaffold for rich content. */
  content?: ReactNode;
  controlSize?: ControlSize;
}

export const Button: FC<ButtonProps> = ({
  variant = 'secondary',
  loading = false,
  icon,
  startIcon,
  endIcon,
  children,
  content,
  className,
  disabled,
  controlSize,
  type = 'button',
  ...props
}) => {
  const size = useControlSize(controlSize);
  const styles = buttonVariants({ variant, size });
  return (
    <BaseButton
      {...props}
      type={type}
      data-slot="button"
      data-size={size}
      data-variant={variant}
      aria-busy={loading || undefined}
      className={styles.root({ className })}
      disabled={disabled || loading}
    >
      {loading ? (
        <Icon
          icon={<LoaderCircle />}
          className="animate-spin motion-reduce:animate-none"
        />
      ) : (
        <Icon icon={startIcon ?? icon} />
      )}
      {content === undefined ? (
        <span data-slot="button-label" className={styles.label()}>
          {children}
        </span>
      ) : (
        content
      )}
      <Icon icon={endIcon} />
    </BaseButton>
  );
};
