import { Tabs as BaseTabs } from '@base-ui/react/tabs';
import { useId, type ReactNode } from 'react';
import { tv } from 'tailwind-variants';
import { useControlSize } from '../foundations/ControlProvider';
import { controlSizes, type ControlSize } from '../foundations/utils';

const tabs = tv({
  slots: {
    root: 'min-w-0',
    list: 'flex h-(--control-height) min-w-0 flex-nowrap items-stretch gap-5',
    tab: 'relative inline-flex h-full min-w-0 shrink-0 cursor-pointer items-center justify-center border-0 bg-transparent p-0 text-center leading-5 font-medium whitespace-nowrap text-neutral-500 after:absolute after:inset-x-0 after:bottom-0 after:h-0.5 after:bg-transparent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-(--focus) aria-disabled:cursor-not-allowed aria-disabled:opacity-40 aria-selected:text-neutral-800 aria-selected:after:bg-blue-600',
  },
  variants: {
    size: {
      compact: { root: controlSizes.compact },
      default: { root: controlSizes.default },
      large: { root: controlSizes.large },
    },
  },
  defaultVariants: { size: 'default' },
});

export interface TabOption<Value extends string> {
  value: Value;
  label: ReactNode;
  disabled?: boolean;
  /** The caller owns the corresponding panel and its content. */
  panelId?: string;
}

export interface TabsProps<Value extends string> {
  label: string;
  value: Value;
  options: readonly TabOption<Value>[];
  onChange: (value: Value) => void;
  controlSize?: ControlSize;
  className?: string;
  /** Tab ids follow `${id}-tab-${value}` for external panel labels. */
  id?: string;
}

/** Underlined navigation keeps one height in every selection state. */
export function Tabs<Value extends string>({
  label,
  value,
  options,
  onChange,
  controlSize,
  className,
  id,
}: TabsProps<Value>) {
  const generatedId = useId();
  const tabsId = id ?? generatedId;
  const size = useControlSize(controlSize);
  const styles = tabs({ size });
  return (
    <BaseTabs.Root
      id={tabsId}
      value={value}
      onValueChange={(nextValue) => {
        const option = options.find(
          (item) => item.value === nextValue && !item.disabled,
        );
        if (option) onChange(option.value);
      }}
      className={styles.root({ className })}
      data-size={size}
      data-slot="tabs"
    >
      <BaseTabs.List
        aria-label={label}
        className={styles.list()}
        activateOnFocus
        loopFocus
      >
        {options.map((option) => (
          <BaseTabs.Tab
            key={option.value}
            id={`${tabsId}-tab-${option.value}`}
            type="button"
            value={option.value}
            disabled={option.disabled}
            aria-controls={option.panelId}
            className={styles.tab()}
            data-size={size}
            data-slot="tab"
          >
            {option.label}
          </BaseTabs.Tab>
        ))}
      </BaseTabs.List>
    </BaseTabs.Root>
  );
}
