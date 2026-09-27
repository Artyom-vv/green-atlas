import type { FC } from 'react';
import type { PlanObject } from '@green/api-client';
import { Crosshair, Grid3x3 } from 'lucide-react';
import { Button, FormActions } from '@green/ui';
import { EditorPanel } from '@/shared/ui/inspector';
import { plantingCount } from '@/shared/format/countLabel';

export interface PlantingsOverviewPanelProps {
  objects: PlanObject[];
  onPlace: () => void;
  onFit: () => void;
  onClose?: () => void;
  mapMode?: '2d' | '3d';
}
export const PlantingsOverviewPanel: FC<PlantingsOverviewPanelProps> = ({
  objects,
  onPlace,
  onFit,
  onClose,
  mapMode = '2d',
}) => {
  const trees = objects.filter((object) => object.kind === 'tree').length;
  return (
    <EditorPanel title="План озеленения" onClose={onClose}>
      <strong className="text-base font-semibold">
        {plantingCount(objects.length)}
      </strong>
      {objects.length ? (
        <>
          <dl className="m-0 grid grid-cols-[minmax(0,1fr)_auto] gap-2 text-xs [&_dd]:m-0 [&_dd]:font-mono [&_dt]:text-neutral-600">
            <dt>Деревья</dt>
            <dd>{trees}</dd>
            <dt>Кустарники</dt>
            <dd>{objects.length - trees}</dd>
          </dl>
          <p className="m-0 text-xs text-neutral-600">
            Выберите группу или участок
          </p>
        </>
      ) : (
        <p className="m-0 text-xs text-neutral-600">Создайте первую схему</p>
      )}
      <FormActions layout="equal" minItemWidth="100%">
        <Button variant="primary" icon={Grid3x3} onClick={onPlace}>
          {mapMode === '3d' ? 'Разместить посадки в 2D' : 'Разместить посадки'}
        </Button>
        {!!objects.length && (
          <Button variant="secondary" icon={Crosshair} onClick={onFit}>
            Показать посадки
          </Button>
        )}
      </FormActions>
    </EditorPanel>
  );
};
