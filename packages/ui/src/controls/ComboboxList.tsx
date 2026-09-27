import { Combobox as BaseCombobox } from '@base-ui/react/combobox';
import { Check } from 'lucide-react';
import type { FC } from 'react';
import { Icon } from '../foundations/Icon';
import type { ComboboxOption } from './combobox.types';
interface ComboboxListProps {
  emptyLabel: string;
}
export const ComboboxList: FC<ComboboxListProps> = ({ emptyLabel }) => (
  <BaseCombobox.Portal>
    <BaseCombobox.Positioner
      sideOffset={4}
      className="z-menu w-(--anchor-width) max-w-(--available-width)"
    >
      <BaseCombobox.Popup className="rounded-card max-h-[min(240px,var(--available-height))] overflow-y-auto border border-solid border-neutral-300 bg-white text-sm text-neutral-800 shadow-lg outline-none">
        <BaseCombobox.Empty className="p-3 text-xs text-neutral-500">
          {emptyLabel}
        </BaseCombobox.Empty>
        <BaseCombobox.List>
          {(item: ComboboxOption) => (
            <BaseCombobox.Item
              key={item.value}
              value={item}
              className="flex min-h-10 cursor-pointer items-center justify-between gap-2 px-3 py-2 outline-none data-[disabled]:cursor-not-allowed data-[highlighted]:bg-neutral-100 data-[selected]:bg-blue-100"
            >
              <span className="flex min-w-0 flex-col gap-1">
                <span className="text-sm">{item.label}</span>
                {!!item.description && (
                  <span className="text-xs text-neutral-500">
                    {item.description}
                  </span>
                )}
              </span>
              <BaseCombobox.ItemIndicator>
                <Icon icon={<Check />} />
              </BaseCombobox.ItemIndicator>
            </BaseCombobox.Item>
          )}
        </BaseCombobox.List>
      </BaseCombobox.Popup>
    </BaseCombobox.Positioner>
  </BaseCombobox.Portal>
);
