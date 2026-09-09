import type { PlanObject } from '@green/api-client';
import { Crosshair, Grid3x3 } from 'lucide-react';
import { Button } from '@green/ui';
import { EditorActions, EditorPanel } from './EditorPanel';
import { plantingCount } from './countLabel';

export function PlantingsOverviewPanel({ objects, onPlace, onFit, onClose, mapMode = '2d' }: {
  objects: PlanObject[]; onPlace: () => void; onFit: () => void; onClose: () => void;
  mapMode?: '2d' | '3d';
}) {
  const trees = objects.filter(object => object.kind === 'tree').length;
  return <EditorPanel title="План озеленения" onClose={onClose}>
    <div className="editor-panel__summary">{plantingCount(objects.length)}</div>
    {objects.length ? <><dl className="editor-panel__metrics"><dt>Деревья</dt><dd>{trees}</dd><dt>Кустарники</dt><dd>{objects.length - trees}</dd></dl><p className="editor-panel__hint">Выберите группу или участок</p></> : <p className="editor-panel__hint">Создайте первую схему</p>}
    <EditorActions><Button variant="primary" controlSize="compact" icon={Grid3x3} onClick={onPlace}>{mapMode === '3d' ? 'Разместить посадки в 2D' : 'Разместить посадки'}</Button></EditorActions>
    {objects.length ? <EditorActions><Button variant="secondary" controlSize="compact" icon={Crosshair} onClick={onFit}>Показать посадки</Button></EditorActions> : null}
  </EditorPanel>;
}
