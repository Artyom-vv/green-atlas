import { Checkbox, Dialog, Text } from '@green/ui';
import type { SceneSnapshot } from '@green/api-client';
import { Fragment, type FC } from 'react';
import { sceneSourceFacts } from '@/widgets/scene/model/scenePresentation';

export interface SceneDataDialogProps {
  open: boolean;
  snapshot?: SceneSnapshot;
  zoneCount: number;
  showRoots: boolean;
  onRootsChange: (show: boolean) => void;
  onClose: () => void;
}

export const SceneDataDialog: FC<SceneDataDialogProps> = ({
  open,
  snapshot,
  zoneCount,
  showRoots,
  onRootsChange,
  onClose,
}) => (
  <Dialog open={open} title="Данные сцены" onClose={onClose}>
    <div className="grid gap-4">
      <dl className="m-0 grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
        {sceneSourceFacts(snapshot, zoneCount).map(([label, value]) => (
          <Fragment key={label}>
            <dt className="text-neutral-600">{label}</dt>
            <dd className="m-0 text-right break-words">{value}</dd>
          </Fragment>
        ))}
      </dl>
      <Checkbox
        label="Показать корни"
        checked={showRoots}
        onChange={(event) => onRootsChange(event.target.checked)}
      />
      <Text as="p">
        Перетаскивание — перемещение карты. Правая кнопка — поворот. Колесо —
        масштаб.
      </Text>
      <Text as="p">
        Синий контур — выбор, оранжевый — риск, красный — конфликт. Посадки
        редактируются в 2D.
      </Text>
    </div>
  </Dialog>
);
