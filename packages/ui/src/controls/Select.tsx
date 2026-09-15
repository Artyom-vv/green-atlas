import { ChevronDown } from 'lucide-react';
import type { FC, Ref, SelectHTMLAttributes } from 'react';
import { useFieldControl } from '../forms/fieldContext';
import { useControlSize } from '../foundations/ControlProvider';
import { Icon as IconContent } from '../foundations/Icon';
import { type ControlSize, cx } from '../foundations/utils';
import { input } from './inputVariants';
export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  controlSize?: ControlSize;
  ref?: Ref<HTMLSelectElement>;
}

export const Select: FC<SelectProps> = ({
  className,
  children,
  controlSize,
  ...props
}) => {
  const size = useControlSize(controlSize);
  const fieldProps = useFieldControl(props);
  return (
    <span
      data-slot="select-shell"
      className="relative block min-w-0 shrink-0 text-neutral-800 has-[:disabled]:text-neutral-500"
    >
      <select
        {...fieldProps}
        className={cx(
          input({ size }),
          'h-(--control-height) cursor-pointer appearance-none pr-9',
          className,
        )}
        data-slot="select"
        data-size={size}
      >
        {children}
      </select>
      <span
        className="pointer-events-none absolute inset-y-0 right-3 flex items-center"
        aria-hidden="true"
      >
        <IconContent icon={ChevronDown} />
      </span>
    </span>
  );
};
