import { Check } from 'lucide-react';
import {
  useId,
  type ComponentPropsWithRef,
  type FC,
  type ReactNode,
} from 'react';
import { useFieldControl } from '../forms/fieldContext';
import { useControlSize } from '../foundations/ControlProvider';
import { Icon } from '../foundations/Icon';
import { controlSizes, cx, type ControlSize } from '../foundations/utils';

export interface ChoiceInputProps extends Omit<
  ComponentPropsWithRef<'input'>,
  'type'
> {
  type: 'checkbox' | 'radio';
  label?: ReactNode;
  description?: ReactNode;
  controlSize?: ControlSize;
}

/** Native choices keep browser group semantics; descriptions stay outside their names. */
export const ChoiceInput: FC<ChoiceInputProps> = ({
  type,
  label,
  description,
  className,
  controlSize,
  ...props
}) => {
  const size = useControlSize(controlSize);
  const descriptionId = useId();
  const fieldProps = useFieldControl(props);
  return (
    <div className="grid min-w-0 gap-1">
      <label
        data-slot={type}
        data-size={size}
        className={cx(
          'relative inline-flex cursor-pointer items-center gap-2 text-neutral-700 select-none has-[:disabled]:cursor-not-allowed has-[:disabled]:text-neutral-400',
          controlSizes[size],
          className,
        )}
      >
        <input
          {...fieldProps}
          type={type}
          aria-describedby={
            [
              fieldProps['aria-describedby'],
              description ? descriptionId : undefined,
            ]
              .filter(Boolean)
              .join(' ') || undefined
          }
          className="peer cursor-inherit absolute inset-0 z-[1] m-0 h-full w-full opacity-0"
        />
        <span
          data-slot={`${type}-indicator`}
          className={cx(
            'inline-flex size-4 shrink-0 items-center justify-center border border-solid border-neutral-400 bg-white text-transparent transition peer-checked:border-blue-600 peer-checked:bg-blue-600 peer-checked:text-white peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-(--focus) peer-disabled:border-neutral-300 peer-disabled:bg-neutral-100',
            type === 'radio' ? 'rounded-full' : 'rounded-control',
          )}
        >
          {type === 'radio' ? (
            <span className="size-1.5 rounded-full bg-current" />
          ) : (
            <Icon icon={<Check />} size={12} />
          )}
        </span>
        {label !== undefined && (
          <span className="min-w-0 wrap-anywhere">{label}</span>
        )}
      </label>
      {!!description && (
        <div
          id={descriptionId}
          className="pl-6 text-xs leading-4 text-neutral-500"
        >
          {description}
        </div>
      )}
    </div>
  );
};
