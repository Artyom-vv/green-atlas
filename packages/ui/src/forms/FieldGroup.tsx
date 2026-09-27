import { type ComponentPropsWithRef, type FC, type ReactNode } from 'react';
import { cx } from '../foundations/utils';

export interface FieldGroupProps extends ComponentPropsWithRef<'fieldset'> {
  legend?: ReactNode;
  children: ReactNode;
}

export const FieldGroup: FC<FieldGroupProps> = ({
  legend,
  children,
  className,
  ...props
}) => (
  <fieldset
    className={cx('grid min-w-0 gap-4 border-0 p-0', className)}
    {...props}
  >
    {!!legend && (
      <legend className="mb-1 text-sm font-semibold text-neutral-800">
        {legend}
      </legend>
    )}
    {children}
  </fieldset>
);
