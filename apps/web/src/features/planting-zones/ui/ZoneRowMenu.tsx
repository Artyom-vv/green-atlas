import { useId, type FC } from 'react';
import {
  MoreHorizontal,
  Pencil,
  ScanLine,
  Trash2,
  SlidersHorizontal,
} from 'lucide-react';
import {
  IconButton,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuItem,
  MenuSeparator,
} from '@green/ui';

export interface ZoneRowMenuProps {
  label: string;
  saving: boolean;
  deleteReason?: string;
  onRename: () => void;
  onConditions?: () => void;
  onRedraw: () => void;
  onDelete: () => void;
}
export const ZoneRowMenu: FC<ZoneRowMenuProps> = ({
  label,
  saving,
  deleteReason,
  onRename,
  onConditions,
  onRedraw,
  onDelete,
}) => {
  const reasonId = useId();
  const reason = saving ? 'Дождитесь завершения сохранения' : deleteReason;
  return (
    <Menu modal={false}>
      <MenuTrigger
        render={
          <IconButton
            icon={MoreHorizontal}
            label={`Действия с участком ${label}`}
            variant="ghost"
          />
        }
      />
      <MenuPopup
        align="end"
        aria-label={`Действия с участком ${label}`}
        className="max-w-[min(320px,calc(100vw-24px))]"
      >
        <MenuItem startIcon={<Pencil />} disabled={saving} onClick={onRename}>
          Переименовать
        </MenuItem>
        <MenuItem startIcon={<ScanLine />} disabled={saving} onClick={onRedraw}>
          Изменить контур
        </MenuItem>
        {onConditions && (
          <MenuItem
            startIcon={<SlidersHorizontal />}
            disabled={saving}
            onClick={onConditions}
          >
            Условия участка
          </MenuItem>
        )}
        <MenuSeparator />
        <MenuItem
          startIcon={<Trash2 />}
          disabled={Boolean(reason)}
          aria-describedby={reason ? reasonId : undefined}
          className="text-error"
          onClick={onDelete}
        >
          Удалить участок
        </MenuItem>
        {reason && (
          <p
            id={reasonId}
            className="m-0 px-2 py-1 text-[11px] leading-4 text-neutral-600"
          >
            {reason}
          </p>
        )}
      </MenuPopup>
    </Menu>
  );
};
