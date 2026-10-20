import { Dialog as BaseDialog } from '@base-ui/react/dialog';
import type { FC, ReactNode } from 'react';
import { useRef } from 'react';
import { ControlProvider } from '../foundations/ControlProvider';
import type { ControlSize } from '../foundations/utils';
import { DialogHeader } from './DialogHeader';
import { dialog, type DialogSize } from './dialogVariants';
import { useDialogLayer } from './useDialogLayer';
export type { DialogSize } from './dialogVariants';
export interface DialogProps {
  open: boolean;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  header?: ReactNode;
  onClose: () => void;
  size?: DialogSize;
  keepMounted?: boolean;
  stableHeight?: boolean | 'compact';
  /** Canvas and media own their internal spacing; ordinary content keeps the standard inset. */
  bodyInset?: 'default' | 'none';
  /** Недоступное закрытие отображается явно, пока сценарий подтверждает запись. */
  closeDisabled?: boolean;
  /** Modal controls use one density independent of the opening workspace. */
  controlSize?: ControlSize;
}

export const Dialog: FC<DialogProps> = ({
  open,
  title,
  children,
  footer,
  header,
  onClose,
  size = 'default',
  keepMounted = false,
  stableHeight = false,
  bodyInset = 'default',
  closeDisabled = false,
  controlSize = 'default',
}) => {
  const popupRef = useRef<HTMLDivElement>(null);
  const isTopLayer = useDialogLayer(open);
  const styles = dialog({ size, stable: stableHeight, bodyInset });
  return (
    <ControlProvider size={controlSize}>
      <BaseDialog.Root
        open={open}
        onOpenChange={(next) => {
          if (!next && !closeDisabled && isTopLayer()) onClose();
        }}
      >
        <BaseDialog.Portal keepMounted={keepMounted}>
          <BaseDialog.Backdrop
            hidden={!open}
            className="z-dialog fixed inset-0 bg-neutral-800/20"
          />
          <BaseDialog.Viewport
            hidden={!open}
            inert={!open}
            className="z-dialog p-overlay-gutter fixed inset-0 grid place-items-center overflow-y-auto overscroll-contain"
          >
            <BaseDialog.Popup
              ref={popupRef}
              initialFocus={popupRef}
              data-slot="dialog"
              data-size={size}
              data-stable-height={stableHeight || undefined}
              className={styles.popup()}
            >
              <DialogHeader
                header={header}
                title={title}
                onClose={onClose}
                closeDisabled={closeDisabled}
              />
              <div data-slot="dialog-body" className={styles.body()}>
                {children}
              </div>
              {footer !== undefined && footer !== null && footer !== false && (
                <footer
                  data-slot="dialog-footer"
                  className="grid min-h-12 min-w-0 shrink-0 gap-3 border-0 border-t border-solid border-neutral-200 px-4 py-2"
                >
                  {footer}
                </footer>
              )}
            </BaseDialog.Popup>
          </BaseDialog.Viewport>
        </BaseDialog.Portal>
      </BaseDialog.Root>
    </ControlProvider>
  );
};
