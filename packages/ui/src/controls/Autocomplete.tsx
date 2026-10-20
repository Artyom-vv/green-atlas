import { Autocomplete as BaseAutocomplete } from '@base-ui/react/autocomplete';
import { LoaderCircle, Search } from 'lucide-react';
import { useState } from 'react';
import { Button } from './Button';
import { TextInput } from './TextInput';

export interface AutocompleteOption {
  value: string;
  label: string;
  description?: string;
}

/** A query and explicit command choice; remote ranking belongs to the caller. */
export function Autocomplete({
  id,
  value,
  options,
  open,
  disabled,
  loading,
  error,
  onChange,
  onSelect,
  onOpenChange,
  onRetry,
  placeholder,
}: {
  id: string;
  value: string;
  options: AutocompleteOption[];
  open: boolean;
  disabled?: boolean;
  loading?: boolean;
  error?: string;
  placeholder?: string;
  onChange: (value: string) => void;
  onSelect: (option: AutocompleteOption) => void;
  onOpenChange: (open: boolean) => void;
  onRetry?: () => void;
}) {
  const [keyboardHighlight, setKeyboardHighlight] = useState(false);
  return (
    <BaseAutocomplete.Root
      items={options}
      filter={null}
      value={value}
      itemToStringValue={(item) => item.label}
      autoHighlight="always"
      highlightItemOnHover={false}
      disabled={disabled}
      open={open}
      onOpenChange={(next, details) => {
        if (details.reason !== 'input-change') {
          onOpenChange(next);
          if (!next) setKeyboardHighlight(false);
        }
      }}
      onValueChange={(next, details) => {
        if (details.reason === 'escape-key') {
          details.cancel();
          if (!open) details.allowPropagation();
          return;
        }
        if (details.reason === 'item-press') {
          onOpenChange(false);
          return;
        }
        if (
          details.reason === 'input-change' ||
          details.reason === 'clear-press'
        ) {
          setKeyboardHighlight(false);
          onOpenChange(true);
          onChange(next);
        }
      }}
    >
      <BaseAutocomplete.InputGroup className="relative w-full min-w-0">
        {loading ? (
          <LoaderCircle
            aria-hidden="true"
            className="pointer-events-none absolute top-1/2 left-3 z-10 size-4 -translate-y-1/2 animate-spin text-neutral-600 motion-reduce:animate-none"
          />
        ) : (
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute top-1/2 left-3 z-10 size-4 -translate-y-1/2 text-neutral-600"
          />
        )}
        <BaseAutocomplete.Input
          id={id}
          placeholder={placeholder}
          maxLength={256}
          autoComplete="off"
          onFocus={() => onOpenChange(true)}
          onKeyDown={(event) => {
            if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key))
              setKeyboardHighlight(true);
          }}
          render={<TextInput className="pl-9" />}
        />
      </BaseAutocomplete.InputGroup>
      <BaseAutocomplete.Portal>
        <BaseAutocomplete.Positioner
          side="bottom"
          sideOffset={8}
          align="start"
          positionMethod="fixed"
          collisionBoundary={[]}
          collisionAvoidance={{ side: 'none', align: 'shift' }}
          className="z-menu w-(--anchor-width) max-w-(--available-width)"
        >
          <BaseAutocomplete.Popup
            data-slot="autocomplete-popup"
            className="rounded-card max-h-[min(15rem,var(--available-height))] overflow-y-auto border border-neutral-300 bg-white text-sm text-neutral-800 outline-none data-[ending-style]:opacity-0 data-[starting-style]:translate-y-1 data-[starting-style]:opacity-0 motion-safe:transition-[opacity,transform] motion-safe:duration-150"
          >
            <BaseAutocomplete.Empty>
              <div className="py-3 pr-3 pl-9 text-xs text-neutral-600">
                <span role={error ? 'alert' : 'status'}>
                  {loading ? 'Поиск адресов' : error || 'Нет адресов'}
                </span>
              </div>
              {error && onRetry && (
                <Button
                  variant="ghost"
                  controlSize="compact"
                  className="w-full justify-start rounded-none pl-9"
                  onClick={onRetry}
                >
                  Повторить поиск
                </Button>
              )}
            </BaseAutocomplete.Empty>
            <BaseAutocomplete.List>
              {(item) => (
                <BaseAutocomplete.Item
                  key={item.value}
                  value={item}
                  onClick={() => onSelect(item)}
                  onPointerEnter={() => setKeyboardHighlight(false)}
                  className={`flex cursor-pointer flex-col gap-1 bg-white py-2 pr-3 pl-9 outline-none hover:bg-neutral-100 ${keyboardHighlight ? 'data-[highlighted]:bg-neutral-100' : ''}`}
                >
                  <span>{item.label}</span>
                  {item.description && (
                    <span className="text-xs text-neutral-600">
                      {item.description}
                    </span>
                  )}
                </BaseAutocomplete.Item>
              )}
            </BaseAutocomplete.List>
          </BaseAutocomplete.Popup>
        </BaseAutocomplete.Positioner>
      </BaseAutocomplete.Portal>
    </BaseAutocomplete.Root>
  );
}
