import { Tooltip as BaseTooltip } from '@base-ui/react/tooltip';
import { useRender } from '@base-ui/react/use-render';
import type { FC } from 'react';
import { TooltipContent } from '../feedback/TooltipContent';
import { useControlSize } from '../foundations/ControlProvider';
import { Icon, type IconNode } from '../foundations/Icon';
import { buttonVariants } from './buttonVariants';
import type { LinkButtonProps } from './LinkButton';
export interface LinkIconButtonProps extends Omit<
  LinkButtonProps,
  'children' | 'icon' | 'content'
> {
  icon: IconNode;
  label: string;
}

export const LinkIconButton: FC<LinkIconButtonProps> = ({
  render,
  ref,
  variant = 'secondary',
  controlSize,
  icon,
  startIcon,
  endIcon,
  label,
  className,
  ...props
}) => {
  const size = useControlSize(controlSize);
  const styles = buttonVariants({ variant, size, iconOnly: true });
  const element = useRender({
    defaultTagName: 'a',
    render,
    ref,
    props: {
      ...props,
      'aria-label': label,
      'data-slot': 'link-icon-button',
      'data-size': size,
      'data-variant': variant,
      className: styles.root({ className: ['no-underline', className] }),
      children: (
        <>
          <Icon icon={startIcon ?? icon} />
          <Icon icon={endIcon} />
        </>
      ),
    },
  });
  return (
    <BaseTooltip.Root>
      <BaseTooltip.Trigger render={element} delay={0} />
      <TooltipContent>{label}</TooltipContent>
    </BaseTooltip.Root>
  );
};
