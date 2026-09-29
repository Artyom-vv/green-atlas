import type { FC, ReactNode } from 'react';
import { ControlProvider, IconButton, PanelHeader } from '@green/ui';
import { PanelRightClose } from 'lucide-react';

export interface PlacementWorkspaceProps {
  open: boolean;
  showCloseControl?: boolean;
  onClose: () => void;
  manual: ReactNode;
}

/** The public placement entry opens the explicit-species workflow directly. */
export const PlacementWorkspace: FC<PlacementWorkspaceProps> = ({
  open,
  onClose,
  manual,
  showCloseControl = true,
}) => (
    <ControlProvider size="compact">
      <section
        className="flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-white [&_[hidden]]:hidden [&[hidden]]:hidden"
        hidden={!open}
        inert={!open}
        aria-label="Размещение посадок"
      >
        <PanelHeader className="gap-2">
          <h2 className="m-0 min-w-0 flex-1 text-sm font-semibold wrap-anywhere">
            Размещение посадок
          </h2>
          {showCloseControl && (
            <IconButton
              icon={PanelRightClose}
              label="Свернуть размещение"
              variant="ghost"
              onClick={onClose}
            />
          )}
        </PanelHeader>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {manual}
        </div>
      </section>
    </ControlProvider>
);
