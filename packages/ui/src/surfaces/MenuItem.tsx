import { Menu as BaseMenu } from '@base-ui/react/menu';
import type { ComponentProps, FC, ReactNode } from 'react';
import { useControlSize } from '../foundations/ControlProvider';
import { Icon } from '../foundations/Icon';
import { controlSizes, cx, type ControlSize } from '../foundations/utils';

export interface MenuItemProps extends ComponentProps<typeof BaseMenu.Item> {
  startIcon?: ReactNode;
  endIcon?: ReactNode;
  controlSize?: ControlSize;
}

export const MenuItem: FC<MenuItemProps> = ({
  className,
  children,
  startIcon,
  endIcon,
  controlSize,
  ...props
}) => {
  const size = useControlSize(controlSize);
  return (
    <BaseMenu.Item
      {...props}
      data-size={size}
      className={(state) =>
        cx(
          'rounded-control flex cursor-pointer items-center gap-2 px-2 py-1 outline-none data-[disabled]:cursor-not-allowed data-[disabled]:text-neutral-400 data-[highlighted]:bg-blue-100',
          controlSizes[size],
          typeof className === 'function' ? className(state) : className,
        )
      }
    >
      {startIcon !== undefined && startIcon !== null && (
        <Icon icon={startIcon} />
      )}
      <span className="min-w-0 flex-1">{children}</span>
      {endIcon !== undefined && endIcon !== null && <Icon icon={endIcon} />}
    </BaseMenu.Item>
  );
};
