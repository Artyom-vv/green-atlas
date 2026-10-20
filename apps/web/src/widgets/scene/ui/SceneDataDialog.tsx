import { Checkbox, Dialog, Disclosure, Text } from '@green/ui';
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
    <div className="grid gap-6">
      <section aria-label="Источник и состав" className="grid gap-3">
        <Text as="h3" className="font-medium">
          Источник и состав
        </Text>
        <dl className="m-0 grid grid-cols-[minmax(0,1fr)_minmax(0,2fr)] gap-x-4 gap-y-3 text-sm">
          {sceneSourceFacts(snapshot, zoneCount).map(([label, value]) => (
            <Fragment key={label}>
              <dt className="text-neutral-600">{label}</dt>
              <dd className="m-0 break-words">{value}</dd>
            </Fragment>
          ))}
        </dl>
      </section>
      <section aria-label="Отображение" className="grid gap-3">
        <Text as="h3" className="font-medium">
          Отображение
        </Text>
        <Checkbox
          label="Показать корни"
          checked={showRoots}
          onChange={(event) => onRootsChange(event.target.checked)}
        />
        <Text as="p" tone="muted">
          Синий контур обозначает выбор, оранжевый — риск, красный — конфликт
        </Text>
      </section>
      <Disclosure title="Управление в 3D" variant="plain">
        <dl className="m-0 grid grid-cols-[minmax(0,1fr)_minmax(0,2fr)] gap-x-4 gap-y-3 text-sm">
          <dt className="text-neutral-600">Перемещение</dt>
          <dd className="m-0">Перетаскивание</dd>
          <dt className="text-neutral-600">Поворот</dt>
          <dd className="m-0">Правая кнопка мыши</dd>
          <dt className="text-neutral-600">Масштаб</dt>
          <dd className="m-0">Колесо мыши</dd>
        </dl>
        <Text as="p" tone="muted">
          Посадки редактируются в 2D
        </Text>
      </Disclosure>
    </div>
  </Dialog>
);
