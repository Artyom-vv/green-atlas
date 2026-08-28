import type { PlanObject } from '@green/api-client';
import { Copy, Lock, Move, Trash2, Unlock } from 'lucide-react';
import { Button } from '@green/ui';

export function GroupInspector({ objects, disabled, onMove, onCopy, onLock, onDelete }: { objects: PlanObject[]; disabled?: boolean; onMove: () => void; onCopy: () => void; onLock: (locked: boolean) => void; onDelete: () => void }) {
  const locked = objects.filter((object) => object.locked).length;
  const trees = objects.filter((object) => object.kind === 'tree').length;
  const shrubs = objects.length - trees;
  return <div className="project-inspector multi-selection-inspector">
    <header><span><strong>Выбрано посадок</strong><small>{objects.length} объектов</small></span></header>
    <section className="group-selection-summary">
      <h3>Состав группы</h3>
      <dl><dt>Деревья</dt><dd>{trees}</dd><dt>Кустарники</dt><dd>{shrubs}</dd>{locked ? <><dt>Закреплено</dt><dd>{locked}</dd></> : null}</dl>
    </section>
    <section className="group-selection-actions">
      <Button variant="secondary" icon={Move} disabled={disabled || locked > 0} onClick={onMove}>Переместить</Button>
      <Button variant="secondary" icon={Copy} disabled={disabled} onClick={onCopy}>Копировать</Button>
      <Button variant="secondary" icon={locked === objects.length ? Unlock : Lock} disabled={disabled} onClick={() => onLock(locked !== objects.length)}>{locked === objects.length ? 'Открепить' : 'Закрепить'}</Button>
      <Button variant="danger" icon={Trash2} disabled={disabled || locked > 0} onClick={onDelete}>Удалить выбранные</Button>
    </section>
  </div>;
}
