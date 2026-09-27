import { PlacementChoice } from './PlacementChoice';
import { useState, type FC, type ReactNode } from 'react';
import { ControlProvider, IconButton, PanelHeader } from '@green/ui';
import { ArrowLeft, PanelRightClose } from 'lucide-react';

export interface PlacementWorkspaceProps {
  open: boolean;
  hasPreview: boolean;
  recommendation: boolean;
  busy?: boolean;
  showCloseControl?: boolean;
  onRecommendation: (value: boolean) => void;
  onClose: () => void;
  manual: ReactNode;
  automatic: ReactNode;
}

/** Routes remain mounted so changing the visible task never resets either draft. */
export const PlacementWorkspace: FC<PlacementWorkspaceProps> = ({
  open,
  hasPreview,
  busy = false,
  recommendation,
  onRecommendation,
  onClose,
  manual,
  automatic,
  showCloseControl = true,
}) => {
  const [chosen, setChosen] = useState(false);
  const choose = (value: boolean) => {
    onRecommendation(value);
    setChosen(true);
  };
  const choosing = !chosen && !hasPreview;
  return (
    <ControlProvider size="compact">
      <section
        className="flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-white [&_[hidden]]:hidden [&[hidden]]:hidden"
        hidden={!open}
        inert={!open}
        aria-label="Размещение посадок"
      >
        <PanelHeader className="gap-2">
          {!choosing && (
            <IconButton
              icon={ArrowLeft}
              label="Способ подбора"
              variant="ghost"
              disabled={busy || hasPreview}
              onClick={() => setChosen(false)}
            />
          )}
          <h2 className="m-0 min-w-0 flex-1 text-sm font-semibold wrap-anywhere">
            {choosing
              ? 'Размещение посадок'
              : recommendation
                ? 'Подбор по задаче'
                : 'Размещение посадок'}
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
        {choosing && <PlacementChoice busy={busy} onChoose={choose} />}
        <div
          className="flex min-h-0 min-w-0 flex-1 flex-col"
          hidden={choosing}
          inert={choosing}
        >
          <div
            className="flex min-h-0 min-w-0 flex-1 flex-col"
            hidden={recommendation}
            inert={recommendation}
          >
            {manual}
          </div>
          <div
            className="flex min-h-0 min-w-0 flex-1 flex-col"
            hidden={!recommendation}
            inert={!recommendation}
          >
            {automatic}
          </div>
        </div>
      </section>
    </ControlProvider>
  );
};
