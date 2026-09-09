import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { WorkspaceExplorer } from './WorkspaceExplorer';
afterEach(cleanup);
it('opens dedicated modules instead of expanding object trees in the sidebar', () => {
  const onManagePlantings = vi.fn(), onManageZones = vi.fn();
  render(<WorkspaceExplorer objects={[]} zones={[]} selectedIds={[]} selectedZoneIds={[]} speciesNames={new Map()} layers={null} sourceCount={9340} onSelect={vi.fn()} onZone={vi.fn()} onManageZones={onManageZones} onManagePlantings={onManagePlantings} onSource={vi.fn()} onClose={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: /Посадки проекта/ }));
  expect(onManagePlantings).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: /Рабочие участки/ }));
  expect(onManageZones).toHaveBeenCalledOnce();
  expect(screen.queryByRole('button', { name: /Показать состав/ })).not.toBeInTheDocument();
});
