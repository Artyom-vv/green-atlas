import { Button as BaseButton } from '@base-ui/react/button';
import { Tooltip as BaseTooltip } from '@base-ui/react/tooltip';
import { LoaderCircle } from 'lucide-react';
import type { FC } from 'react';
import { TooltipContent } from '../feedback/TooltipContent';
import { useControlSize } from '../foundations/ControlProvider';
import { Icon, type IconNode } from '../foundations/Icon';
import type { ButtonProps } from './Button';
import { buttonVariants } from './buttonVariants';
import { useIconTooltip } from './useIconTooltip';
export interface IconButtonProps extends Omit<
  ButtonProps,
  'icon' | 'children' | 'content'
> {
  icon: IconNode;
  label: string;
  active?: boolean;
}

export const IconButton: FC<IconButtonProps> = ({
  icon,
  label,
  active = false,
  variant = 'secondary',
  controlSize,
  className,
  onMouseEnter,
  onMouseLeave,
  onFocus,
  onBlur,
  startIcon,
  endIcon,
  loading = false,
  disabled,
  type = 'button',
  ...props
}) => {
  const size = useControlSize(controlSize);
  const tooltip = useIconTooltip({
    disabled: Boolean(disabled || loading),
    'aria-expanded': props['aria-expanded'],
    'aria-describedby': props['aria-describedby'],
    onMouseEnter,
    onMouseLeave,
    onFocus,
    onBlur,
  });
  const styles = buttonVariants({ variant, size, iconOnly: true, active });
  return (
    <BaseTooltip.Root open={tooltip.open}>
      <BaseTooltip.Trigger
        {...props}
        {...tooltip.triggerProps}
        render={<BaseButton type={type} disabled={disabled || loading} />}
        className={styles.root({ className })}
        aria-label={label}
        aria-busy={loading || undefined}
        data-slot="icon-button"
        data-size={size}
        data-variant={variant}
        data-active={active || undefined}
        disabled={disabled || loading}
        tabIndex={disabled || loading ? -1 : props.tabIndex}
      >
        <Icon
          icon={loading ? <LoaderCircle /> : (startIcon ?? icon)}
          className={
            loading ? 'animate-spin motion-reduce:animate-none' : undefined
          }
        />
        <Icon icon={endIcon} />
      </BaseTooltip.Trigger>
      {tooltip.open && <TooltipContent id={tooltip.id}>{label}</TooltipContent>}
    </BaseTooltip.Root>
  );
};
