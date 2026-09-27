import { Combobox as BaseCombobox } from '@base-ui/react/combobox';
import { ChevronDown } from 'lucide-react';
import type { FC } from 'react';
import { useState } from 'react';
import { useFieldControl } from '../forms/fieldContext';
import { useControlSize } from '../foundations/ControlProvider';
import { Icon } from '../foundations/Icon';
import { cx } from '../foundations/utils';
import type { ComboboxProps } from './combobox.types';
import { ComboboxList } from './ComboboxList';
import { input } from './inputVariants';
export type { ComboboxOption, ComboboxProps } from './combobox.types';

export const Combobox: FC<ComboboxProps> = ({
  value,
  options,
  placeholder = 'Выберите',
  emptyLabel = 'Ничего не найдено',
  disabled = false,
  controlSize,
  onChange,
  className,
  ...inputProps
}) => {
  const size = useControlSize(controlSize);
  const fieldProps = useFieldControl({ ...inputProps, disabled });
  const [open, setOpen] = useState(false);
  const selected = options.find((option) => option.value === value) ?? null;
  return (
    <BaseCombobox.Root
      open={open}
      onOpenChange={setOpen}
      items={options}
      value={selected}
      name={inputProps.name}
      required={fieldProps.required}
      disabled={fieldProps.disabled}
      autoHighlight
      isItemEqualToValue={(item, next) => item.value === next.value}
      itemToStringLabel={(item) => item.label}
      itemToStringValue={(item) => item.value}
      onInputValueChange={(_, details) => {
        if (!open && details.reason === 'escape-key') {
          details.cancel();
          details.allowPropagation();
        }
      }}
      onValueChange={(next, details) => {
        // This control requires an explicit option. A closed list must not
        // consume the parent task's Escape while trying to clear that option.
        if (!open && details.reason === 'escape-key') {
          details.cancel();
          details.allowPropagation();
          return;
        }
        if (next) onChange(next.value);
      }}
    >
      <BaseCombobox.InputGroup
        data-slot="combobox"
        data-size={size}
        className={cx('relative min-w-0 text-neutral-800', className)}
      >
        <BaseCombobox.Input
          {...fieldProps}
          name={undefined}
          onFocus={() => setOpen(true)}
          className={cx(input({ size }), 'h-(--control-height) pr-9')}
          placeholder={placeholder}
        />
        <BaseCombobox.Trigger
          className="absolute top-0 right-0 flex h-full w-8 cursor-pointer items-center justify-center border-0 bg-transparent p-0 text-current disabled:cursor-not-allowed"
          aria-label="Показать варианты"
        >
          <Icon icon={<ChevronDown />} />
        </BaseCombobox.Trigger>
      </BaseCombobox.InputGroup>
      <ComboboxList emptyLabel={emptyLabel} />
    </BaseCombobox.Root>
  );
};
