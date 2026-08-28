import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PlantingZoneAssignment } from '@green/api-client';
import { PlantingZonesPanel } from './PlantingZonesPanel';

afterEach(cleanup);

function Harness({ onSave = vi.fn(), onManual = vi.fn() }: { onSave?: () => void; onManual?: () => void }) {
  const [assignments, setAssignments] = useState<PlantingZoneAssignment[]>([{ id: 'area-a', label: 'Контур DXF: газон', geometry: { type: 'Polygon', coordinates: [] } }]);
  return <PlantingZonesPanel
    assignments={assignments}
    onRemove={(assignmentId) => setAssignments((current) => current.filter((item) => item.id !== assignmentId))}
    onSave={onSave}
    onManual={onManual}
    onCancelManual={vi.fn()}
  />;
}

describe('PlantingZonesPanel', () => {
  it('opens the editor from a selected local area without asking for a planting programme', () => {
    const onSave = vi.fn();
    render(<Harness onSave={onSave} />);

    expect(screen.getByText('Выбрано', { exact: true })).toBeInTheDocument();
    expect(screen.getByText('Контур DXF: газон')).toBeInTheDocument();
    expect(screen.getByText('Посадки добавляются на карте.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Открыть редактор' }));
    expect(onSave).toHaveBeenCalledOnce();
  });

  it('removes a selected area without opening another step', () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: /Убрать Контур DXF: газон/ }));
    expect(screen.getByText('Начните с области')).toBeInTheDocument();
  });

  it('starts manual geometry from one explicit action', () => {
    const onManual = vi.fn();
    render(<Harness onManual={onManual} />);
    fireEvent.click(screen.getByRole('button', { name: 'Нарисовать область' }));
    expect(onManual).toHaveBeenCalledOnce();
  });
});
