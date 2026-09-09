import type { PlanObject } from '@green/api-client';
import { Crosshair, Leaf, Lock, Move, Trash2, Unlock } from 'lucide-react';
import { Button } from '@green/ui';
import type { GrowthHorizon } from './GrowthHorizonControl';
import { EditorActions, EditorPanel } from './EditorPanel';
import { EditorGrowth } from './EditorGrowth';

export function ObjectInspector({ object, speciesName, growthHorizon, onGrowthHorizon, onSpecies, onDelete, onFit, onMove, onLock, editable = true, mapMode = '2d' }: {
  object?: PlanObject; speciesName?: string; growthHorizon?: GrowthHorizon;
  onGrowthHorizon: (value: GrowthHorizon) => void; onSpecies: () => void; onDelete: () => void;
  onFit?: () => void; onMove?: () => void; onLock?: (locked: boolean) => void; editable?: boolean;
  mapMode?: '2d' | '3d';
}) {
  if (!object) return <EditorPanel title="Выделение"><p className="editor-panel__hint">Ничего не выбрано</p></EditorPanel>;
  const status = object.status === 'error' ? 'Есть нарушение' : object.status === 'warning' ? 'Есть замечания' : 'Размещение допустимо';
  return <EditorPanel title="Выделение">
    {onFit ? <EditorActions><Button variant="secondary" controlSize="compact" icon={Crosshair} onClick={onFit}>К выделению</Button></EditorActions> : null}
    <section className="editor-panel__section">
      <h3>{object.kind === 'tree' ? 'Дерево' : 'Кустарник'}</h3>
      <dl className="editor-panel__metrics"><dt>Вид</dt><dd>{speciesName ?? 'Не назначен'}</dd><dt>Проверка</dt><dd>{status}</dd><dt>Закреплено</dt><dd>{object.locked ? 'Да' : 'Нет'}</dd></dl>
      {editable ? <EditorActions grid>
        <Button className="editor-action-wide" variant="secondary" controlSize="compact" icon={Leaf} onClick={onSpecies}>{speciesName ? 'Изменить вид' : 'Назначить вид'}</Button>
        {onMove ? <Button className={mapMode === '3d' ? 'editor-action-wide' : undefined} variant="secondary" controlSize="compact" icon={Move} onClick={onMove}>{mapMode === '3d' ? 'Переместить в 2D' : 'Переместить'}</Button> : null}
        <Button variant="danger" controlSize="compact" icon={Trash2} onClick={onDelete}>Удалить</Button>
      </EditorActions> : null}
      {onLock ? <EditorActions><Button variant="secondary" controlSize="compact" icon={object.locked ? Unlock : Lock} onClick={() => onLock(!object.locked)}>{object.locked ? 'Открепить' : 'Закрепить'}</Button></EditorActions> : null}
    </section>
    <section className="editor-panel__section"><EditorGrowth objects={[object]} value={growthHorizon} onChange={onGrowthHorizon} showControl={mapMode === '2d'} /></section>
  </EditorPanel>;
}
