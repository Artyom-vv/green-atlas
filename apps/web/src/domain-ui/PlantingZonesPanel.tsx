import type { PlantingZoneAssignment } from '@green/api-client';
import { Edit3, Trash2 } from 'lucide-react';
import { Button, IconButton, InlineMessage } from '@green/ui';
import { repeatedItemLabel } from './plantingZoneLabels';
import { EditorActions, EditorPanel } from './EditorPanel';
import './editor-zones.css';

function SelectedZone({ assignment, assignments, onRemove }: { assignment: PlantingZoneAssignment; assignments: PlantingZoneAssignment[]; onRemove: () => void }) {
  const label = repeatedItemLabel(assignments, assignment);
  return <div className="editor-zone-draft">
    <span><strong>{label}</strong></span>
    <IconButton icon={Trash2} label={`Убрать ${label}`} variant="ghost" controlSize="compact" onClick={onRemove} />
  </div>;
}

export function PlantingZonesPanel({ assignments, drawingManual = false, saving = false, error, onRemove, onSave, onManual, onCancelManual, onClose }: { assignments: PlantingZoneAssignment[]; drawingManual?: boolean; saving?: boolean; error?: string; onRemove: (assignmentId: string) => void; onSave: () => void; onManual: () => void; onCancelManual: () => void; onClose?: () => void }) {
  return <EditorPanel title="Выберите место" onClose={onClose}>
    {assignments.length ? <><h3>Выбрано {assignments.length}</h3>{assignments.map(assignment => <SelectedZone key={assignment.id ?? assignment.label} assignment={assignment} assignments={assignments} onRemove={() => onRemove(assignment.id ?? '')} />)}</> : <p className="editor-panel__hint">Кликните контур на карте или нарисуйте участок.</p>}
    {drawingManual ? <><p className="editor-panel__hint">Поставьте точки и замкните контур</p><EditorActions><Button variant="secondary" onClick={onCancelManual}>Отмена</Button></EditorActions></> : <EditorActions><Button variant="secondary" icon={Edit3} onClick={onManual}>Нарисовать область</Button></EditorActions>}
    {error ? <InlineMessage tone="error">{error}</InlineMessage> : null}
    {assignments.length ? <EditorActions><Button variant="primary" loading={saving} disabled={saving} onClick={onSave}>Открыть редактор</Button></EditorActions> : null}
  </EditorPanel>;
}
