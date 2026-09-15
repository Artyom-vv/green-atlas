import { useState, type FC } from 'react';
import type { SpeciesRevision } from '@green/api-client';
import { Button, Dialog, tv } from '@green/ui';
import { Search } from 'lucide-react';
import { SpeciesPhoto } from './SpeciesPhoto';
import { SpeciesCatalog } from './SpeciesCatalog';

const picker = tv({
  base: 'grid h-auto min-h-16 w-full gap-2 p-2 text-left whitespace-normal',
  variants: {
    selected: {
      true: 'grid-cols-[44px_minmax(0,1fr)_16px]',
      false: 'grid-cols-[minmax(0,1fr)_16px]',
    },
  },
});

export interface SpeciesPickerProps {
  species: SpeciesRevision[];
  value?: string;
  onChange: (id: string) => void;
  disabled?: boolean;
  label?: string;
  onBrowse?: () => void;
}

export const SpeciesPicker: FC<SpeciesPickerProps> = ({
  species,
  value,
  onChange,
  disabled,
  onBrowse,
  label = 'Выбрать породу',
}) => {
  const [open, setOpen] = useState(false);
  const selected = species.find((item) => item.id === value);
  return (
    <>
      <Button
        variant="secondary"
        className={picker({ selected: Boolean(selected) })}
        endIcon={<Search />}
        aria-label={label}
        disabled={disabled}
        onClick={() => (onBrowse ? onBrowse() : setOpen(true))}
        content={
          <>
            {selected && (
              <SpeciesPhoto
                key={selected.id}
                species={selected}
                size="picker"
              />
            )}
            <span className="grid min-w-0 flex-1 gap-0.5 wrap-anywhere">
              <strong className="text-xs leading-4 font-medium">
                {selected?.common_name ?? 'Выбрать породу'}
              </strong>
              <small className="text-[11px] leading-4 text-neutral-600">
                {selected?.scientific_name ?? 'Каталог с фотографиями'}
              </small>
            </span>
          </>
        }
      />
      {!onBrowse && (
        <Dialog
          open={open}
          title="Каталог пород"
          size="wide"
          stableHeight
          onClose={() => setOpen(false)}
        >
          <SpeciesCatalog
            layout="fill"
            species={species}
            value={value}
            onChange={(id) => {
              onChange(id);
              setOpen(false);
            }}
          />
        </Dialog>
      )}
    </>
  );
};
