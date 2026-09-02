import type { PlanObject } from '@green/api-client';
import { Crosshair } from 'lucide-react';
import { Button, EmptyState } from '@green/ui';
import { InspectorHeader } from './InspectorHeader';

export function PlantingsOverviewPanel({ objects, onPlace, onFit, onClose }: {
  objects: PlanObject[];
  onPlace: () => void;
  onFit: () => void;
  onClose: () => void;
}) {
  return <div className="plantings-overview">
    <InspectorHeader title="План озеленения" meta={`${objects.length} посадок`} onClose={onClose} />
    {objects.length ? <div className="plantings-overview__body">
      <section className="plantings-overview__primary">
        <span className="plantings-overview__copy">
          <strong>Выберите группу или участок</strong>
          <span>Проверьте или измените посадки</span>
        </span>
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
