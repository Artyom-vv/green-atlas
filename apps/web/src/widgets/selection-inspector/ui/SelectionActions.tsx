import type { FC } from 'react';
import { Button, FormActions } from '@green/ui';
import { Copy, Leaf, Lock, Move, Trash2, Unlock } from 'lucide-react';

export interface SelectionActionsProps {
  label: string;
  speciesLabel: string;
  lockLabel?: string;
  locked?: boolean;
  disabled?: boolean;
  editDisabled?: boolean;
  mapMode: '2d' | '3d';
  onSpecies?: () => void;
  onMove?: () => void;
  onCopy?: () => void;
  onLock?: () => void;
  onDelete?: () => void;
}

export const SelectionActions: FC<SelectionActionsProps> = ({
  label,
  speciesLabel,
  lockLabel,
  locked,
  disabled,
  editDisabled,
  mapMode,
  onSpecies,
  onMove,
  onCopy,
  onLock,
  onDelete,
}) => (
  <section aria-label={label} className="grid min-w-0 gap-2">
    {onSpecies && (
      <Button
        variant="primary"
        icon={Leaf}
        disabled={disabled || editDisabled}
        onClick={onSpecies}
      >
        {speciesLabel}
      </Button>
    )}
    <FormActions layout="equal" minItemWidth="11rem">
      {onMove && (
        <Button
          variant="secondary"
          icon={Move}
          disabled={disabled || editDisabled}
          onClick={onMove}
        >
          {mapMode === '3d' ? 'Переместить в 2D' : 'Переместить'}
        </Button>
      )}
      {onCopy && (
        <Button
          variant="secondary"
          icon={Copy}
          disabled={disabled}
          onClick={onCopy}
        >
          {mapMode === '3d' ? 'Копировать в 2D' : 'Копировать'}
        </Button>
      )}
      {onLock && (
        <Button
          variant="secondary"
          icon={locked ? Unlock : Lock}
          disabled={disabled}
          onClick={onLock}
        >
          {lockLabel ?? (locked ? 'Открепить' : 'Закрепить')}
        </Button>
      )}
      {onDelete && (
        <Button
          variant="danger"
          icon={Trash2}
          disabled={disabled || editDisabled}
          onClick={onDelete}
        >
          Удалить
        </Button>
      )}
    </FormActions>
  </section>
);
