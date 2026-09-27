import { Menu as BaseMenu } from '@base-ui/react/menu';
import type { ComponentProps, FC } from 'react';
import { cx } from '../foundations/utils';

export const Menu = BaseMenu.Root;

export const MenuTrigger = BaseMenu.Trigger;

export const MenuGroup = BaseMenu.Group;

export const MenuGroupLabel = BaseMenu.GroupLabel;

export const MenuCheckboxItem = BaseMenu.CheckboxItem;

export const MenuRadioGroup = BaseMenu.RadioGroup;

export const MenuRadioItem = BaseMenu.RadioItem;

export interface MenuSeparatorProps extends ComponentProps<
  typeof BaseMenu.Separator
> {}

export const MenuSeparator: FC<MenuSeparatorProps> = ({
  className,
  ...props
}) => (
  <BaseMenu.Separator
    {...props}
    className={(state) =>
      cx(
        'my-1 h-px border-0 bg-neutral-200',
        typeof className === 'function' ? className(state) : className,
      )
    }
  />
);
