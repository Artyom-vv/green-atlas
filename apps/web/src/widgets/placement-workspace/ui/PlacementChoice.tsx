import type { FC } from 'react';
import { Button } from '@green/ui';
import { SlidersHorizontal, Sparkles } from 'lucide-react';
interface PlacementChoiceProps {
  busy: boolean;
  onChoose: (value: boolean) => void;
}
export const PlacementChoice: FC<PlacementChoiceProps> = ({
  busy,
  onChoose,
}) => (
  <div className="grid min-h-0 content-start gap-4 overflow-auto overscroll-contain px-3 py-5">
    <h3 className="m-0 text-base font-semibold">Как подобрать посадки?</h3>
    <div className="grid min-w-0 gap-3">
      <Button
        variant="secondary"
        startIcon={<SlidersHorizontal />}
        className="justify-start text-left"
        disabled={busy}
        onClick={() => onChoose(false)}
      >
        Выбрать состав и количество
      </Button>
      <Button
        variant="secondary"
        startIcon={<Sparkles />}
        className="justify-start text-left"
        disabled={busy}
        onClick={() => onChoose(true)}
      >
        Подобрать по задаче
      </Button>
    </div>
  </div>
);
