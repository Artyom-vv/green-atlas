import type { PlanObject } from '@green/api-client';
import { Button, EmptyState } from '@green/ui';

export function ObjectInspector({ object, onMove, onDelete, editable = true }: { object?: PlanObject; onMove: () => void; onDelete: () => void; editable?: boolean }) {
  if (!object) return <div className="inspector-empty"><EmptyState title="Ничего не выбрано" description="Выберите объект на карте, чтобы увидеть его параметры." /></div>;
  const status = object.status === 'error'
    ? { className: 'is-error', label: 'Есть нарушение', description: 'Позиция не проходит обязательную геометрическую проверку.' }
    : object.status === 'warning'
      ? { className: 'is-warning', label: 'Нужно уточнение', description: 'Есть объект DXF, для которого пока нет достаточных данных для нормативной проверки.' }
      : { className: 'is-valid', label: 'Размещение допустимо', description: 'Нарушений обязательных расстояний не обнаружено.' };
  return (
    <div className="object-inspector">
      <header><span><strong>{object.kind === 'tree' ? 'Дерево' : 'Кустарник'}</strong><small>Выбранная посадка</small></span></header>
      <section className="inspector-status"><span>Проверка</span><strong className={status.className}><i />{status.label}</strong><p>{status.description}</p></section>
      <div className="inspector-spacer" />
      <footer>{editable ? <><button type="button" onClick={onDelete}>Удалить</button><Button variant="primary" onClick={onMove}>Переместить</Button></> : <Button variant="secondary" disabled>Зафиксировано в реализации</Button>}</footer>
    </div>
  );
}
