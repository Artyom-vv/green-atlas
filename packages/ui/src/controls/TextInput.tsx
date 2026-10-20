import type { FC, InputHTMLAttributes, ReactNode, Ref } from 'react';
import { useFieldControl } from '../forms/fieldContext';
import { useControlSize } from '../foundations/ControlProvider';
import { Icon as IconContent } from '../foundations/Icon';
import { type ControlSize, cx } from '../foundations/utils';
import { textInput } from './inputVariants';
export interface TextInputProps extends Omit<
  InputHTMLAttributes<HTMLInputElement>,
  'size'
> {
  size?: number;
  controlSize?: ControlSize;
  variant?: 'default' | 'ghost';
  ref?: Ref<HTMLInputElement>;
  startIcon?: ReactNode;
  endIcon?: ReactNode;
}

export const TextInput: FC<TextInputProps> = ({
  className,
  controlSize,
  variant = 'default',
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
        textInput({ size, variant }),
        'h-(--control-height)',
        Boolean(startIcon) && (variant === 'ghost' ? 'pl-6' : 'pl-9'),
        Boolean(endIcon) && (variant === 'ghost' ? 'pr-6' : 'pr-9'),
        className,
      )}
      data-size={size}
      data-variant={variant}
    />
  );
  if (!startIcon && !endIcon) return control;
  return (
    <span className="relative inline-flex w-full min-w-0 shrink-0 text-neutral-800 has-[:disabled]:text-neutral-500">
      {!!startIcon && (
        <span
          className={cx(
            'pointer-events-none absolute inset-y-0 flex items-center',
            variant === 'ghost' ? 'left-0' : 'left-3',
          )}
        >
          <IconContent icon={startIcon} />
        </span>
      )}
      {control}
      {!!endIcon && (
        <span
          className={cx(
            'pointer-events-none absolute inset-y-0 flex items-center',
            variant === 'ghost' ? 'right-0' : 'right-3',
          )}
        >
          <IconContent icon={endIcon} />
        </span>
      )}
    </span>
  );
};
