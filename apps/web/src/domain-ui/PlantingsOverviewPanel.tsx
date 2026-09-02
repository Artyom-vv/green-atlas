import type { PlanObject } from '@green/api-client';
import { Crosshair, PanelRightClose } from 'lucide-react';
import { Button, EmptyState, IconButton } from '@green/ui';

export function PlantingsOverviewPanel({ objects, onPlace, onFit, onClose }: {
  objects: PlanObject[];
  onPlace: () => void;
  onFit: () => void;
  onClose: () => void;
}) {
  return <div className="plantings-overview">
    <header>
      <span><strong>План озеленения</strong><small>{objects.length} посадок</small></span>
      <IconButton icon={PanelRightClose} label="Свернуть боковую панель" variant="ghost" controlSize="compact" onClick={onClose} />
    </header>
    {objects.length ? <div className="plantings-overview__body">
      <section className="plantings-overview__primary">
        <strong>Выберите группу или участок</strong>
        <span>Проверьте или измените посадки</span>
        <Button variant="primary" onClick={onPlace}>Разместить посадки</Button>
      </section>
      <section className="plantings-overview__secondary">
        <Button variant="secondary" icon={Crosshair} onClick={onFit}>Показать посадки</Button>
      </section>
    </div> : <div className="plantings-overview__empty">
      <EmptyState title="Создайте первую схему" description="Сервис найдёт допустимые места" action={<Button variant="primary" onClick={onPlace}>Разместить посадки</Button>} />
    </div>}
  </div>;
}
