import { Dialog as BaseDialog } from '@base-ui/react/dialog';
import { X } from 'lucide-react';
import type { FC } from 'react';
import { IconButton } from '../controls/IconButton';
import type { DialogProps } from './Dialog';
import { SurfaceHeader } from './SurfaceParts';

interface DialogHeaderProps extends Pick<
  DialogProps,
  'header' | 'title' | 'onClose' | 'closeDisabled'
> {}

export const DialogHeader: FC<DialogHeaderProps> = ({
  header,
  title,
  onClose,
  closeDisabled,
}) =>
  header === undefined ? (
    <SurfaceHeader
      data-slot="dialog-header"
      className="border-0 border-b border-solid"
    >
      <BaseDialog.Title className="m-0 min-w-0 px-2 text-base font-semibold wrap-anywhere">
        {title}
      </BaseDialog.Title>
      <IconButton
        icon={<X />}
        label="Закрыть"
        variant="ghost"
        disabled={closeDisabled}
        onClick={onClose}
      />
    </SurfaceHeader>
  ) : (
    <>
      <BaseDialog.Title className="sr-only">{title}</BaseDialog.Title>
      {header}
    </>
  );
