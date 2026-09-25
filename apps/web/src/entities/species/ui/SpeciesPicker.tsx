import { useState, type FC } from 'react';
import type { SpeciesRevision } from '@green/api-client';
import { Button, Dialog } from '@green/ui';
import { BookOpen } from 'lucide-react';
import { SpeciesSummary } from './SpeciesSummary';
import { SpeciesCatalog } from './SpeciesCatalog';

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
  const action = selected
    ? 'Изменить растение в каталоге'
    : 'Открыть каталог растений';
  return (
    <>
      <div className="grid min-w-0 gap-3">
        {selected && <SpeciesSummary species={selected} />}
        <Button
          variant="secondary"
          className="w-full"
          startIcon={<BookOpen />}
          aria-label={`${action}: ${label}`}
          aria-haspopup="dialog"
          aria-expanded={onBrowse ? undefined : open}
          disabled={disabled}
          onClick={() => (onBrowse ? onBrowse() : setOpen(true))}
        >
          {action}
        </Button>
      </div>
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
