import type { PlantingZoneAssignment } from '@green/api-client';
import type { ReactNode } from 'react';
import { Edit3, Trash2 } from 'lucide-react';
import { Button, EmptyState, IconButton, InlineMessage } from '@green/ui';
import { repeatedItemLabel } from './plantingZoneLabels';

function SelectedZone({ assignment, assignments, onRemove }: { assignment: PlantingZoneAssignment; assignments: PlantingZoneAssignment[]; onRemove: () => void }) {
  const label = repeatedItemLabel(assignments, assignment);
  return <div className="planting-assignment-row">
    <span><strong>{label}</strong></span>
    <IconButton icon={Trash2} label={`Убрать ${label}`} variant="ghost" controlSize="compact" onClick={onRemove} />
  </div>;
}

export function PlantingZonesPanel({ assignments, drawingManual = false, saving = false, error, onRemove, onSave, onManual, onCancelManual }: { assignments: PlantingZoneAssignment[]; drawingManual?: boolean; saving?: boolean; error?: string; onRemove: (assignmentId: string) => void; onSave: () => void; onManual: () => void; onCancelManual: () => void }) {
  return <div className="planting-flow">
    <header className="planting-flow__header">
      <div><h2>Выберите место</h2></div>
    </header>

    <PanelBody className={!assignments.length ? 'planting-flow__body--empty' : undefined}>
      {assignments.length ? <section className="planting-place-group">
        <header><strong>Выбрано</strong><span>{assignments.length}</span></header>
        <div>{assignments.map((assignment) => <SelectedZone key={assignment.id ?? assignment.label} assignment={assignment} assignments={assignments} onRemove={() => onRemove(assignment.id ?? '')} />)}</div>
      </section> : <PanelEmptyState onManual={onManual} />}

      {drawingManual ? <div className="planting-manual-mode"><span><strong>Нарисуйте контур</strong><small>Поставьте точки, затем замкните</small></span><Button variant="ghost" onClick={onCancelManual}>Отмена</Button></div> : assignments.length ? <Button className="planting-flow__manual" variant="ghost" icon={Edit3} onClick={onManual}>Нарисовать область</Button> : null}
      {error ? <InlineMessage tone="error">{error}</InlineMessage> : null}
    </PanelBody>

    {assignments.length ? <PanelFooter>
      <Button variant="primary" loading={saving} disabled={saving || !assignments.length} onClick={onSave}>Открыть редактор</Button>
    </PanelFooter> : null}
  </div>;
}

export function PanelBody({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`planting-flow__content ${className}`.trim()}>{children}</div>;
}

export function PanelFooter({ children }: { children: ReactNode }) {
  return <footer className="planting-flow__footer">{children}</footer>;
}

export function PanelEmptyState({ onManual }: { onManual: () => void }) {
  return <div className="panel-empty-state"><EmptyState title="Выберите область" description="Кликните контур на карте" action={<Button variant="primary" icon={Edit3} onClick={onManual}>Нарисовать область</Button>} /></div>;
}
