import type { FC, InputHTMLAttributes, ReactNode, Ref } from 'react';
import { useFieldControl } from '../forms/fieldContext';
import { useControlSize } from '../foundations/ControlProvider';
import { Icon as IconContent } from '../foundations/Icon';
import { type ControlSize, cx } from '../foundations/utils';
import { input } from './inputVariants';
export interface TextInputProps extends Omit<
  InputHTMLAttributes<HTMLInputElement>,
  'size'
> {
  size?: number;
  controlSize?: ControlSize;
  ref?: Ref<HTMLInputElement>;
  startIcon?: ReactNode;
  endIcon?: ReactNode;
}

export const TextInput: FC<TextInputProps> = ({
  className,
  controlSize,
  startIcon,
  endIcon,
  ...props
}) => {
  const size = useControlSize(controlSize);
  const fieldProps = useFieldControl(props);
  const control = (
    <input
      {...fieldProps}
      data-slot="input"
      className={cx(
        input({ size }),
        'h-(--control-height)',
        Boolean(startIcon) && 'pl-9',
        Boolean(endIcon) && 'pr-9',
        className,
      )}
      data-size={size}
    />
  );
  if (!startIcon && !endIcon) return control;
  return (
    <span className="relative inline-flex w-full min-w-0 shrink-0 text-neutral-800 has-[:disabled]:text-neutral-500">
      {!!startIcon && (
        <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center">
          <IconContent icon={startIcon} />
        </span>
      )}
      {control}
      {!!endIcon && (
        <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center">
          <IconContent icon={endIcon} />
        </span>
      )}
    </span>
  );
};
