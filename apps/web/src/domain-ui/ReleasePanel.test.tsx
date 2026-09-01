import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { Plan, ReleasePackage } from '@green/api-client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ReleasePanel } from './ReleasePanel';

afterEach(cleanup);

const draftPlan = {
  id: 'plan-1',
  version: 3,
  issues: [],
  objects: [{ id: 'tree-1', kind: 'tree', x: 10, y: 20, radius: 1.6, size_class: 'unspecified', species_revision_id: null, spacing_policy: 'balanced', locked: false, status: 'valid' }],
} as Plan;

describe('ReleasePanel', () => {
  it('allows an honest draft while blocking a final package without species', () => {
    const onCreate = vi.fn();
    render(<ReleasePanel plan={draftPlan} onCreate={onCreate} onDownload={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Собрать черновой пакет' }));
    expect(onCreate).toHaveBeenCalledWith({ mode: 'draft', scene_horizon: 0 });
    expect(screen.getByRole('button', { name: 'Собрать финальный пакет' })).toBeDisabled();
    expect(screen.getByText(/не является согласованием/)).toBeVisible();
  });

  it('requires attributable PP-616 and PP-1160 decisions before a final package', () => {
    const onCreate = vi.fn();
    const assignedPlan = { ...draftPlan, objects: [{ ...draftPlan.objects![0], species_revision_id: 'tilia@1' }] } as Plan;
    render(<ReleasePanel plan={assignedPlan} onCreate={onCreate} onDownload={vi.fn()} />);

    const finalButton = screen.getByRole('button', { name: 'Собрать финальный пакет' });
    expect(finalButton).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Решение по ПП-616'), { target: { value: 'not_applicable' } });
    fireEvent.change(screen.getByLabelText('Основание решения по ПП-616'), { target: { value: 'Новые посадки без удаления' } });
    fireEvent.change(screen.getByLabelText('Решение по ПП-1160'), { target: { value: 'not_required' } });
    fireEvent.change(screen.getByLabelText('Основание решения по ПП-1160'), { target: { value: 'Удаление не предусмотрено' } });
    fireEvent.change(screen.getByLabelText('Ответственный за проверку'), { target: { value: 'Иванов И И' } });

    expect(finalButton).toBeEnabled();
    fireEvent.click(finalButton);
    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({
      mode: 'final',
      regulatory_basis: expect.objectContaining({ confirmed_by: 'Иванов И И' }),
    }));
  });

  it('downloads the bundle and exposes its individual files', () => {
    const onDownload = vi.fn();
    const release = {
      id: 'release-1', project_id: 'project-1', plan_version: 3, geometry_version: 1,
      mode: 'draft', status: 'draft', created_at: '2026-08-28T00:00:00Z',
      rule_set_revision: 'rules-1', species_catalog_revision: 'catalog-1', scene_horizon: 20, warnings: [],
      artifacts: [
        { id: 'bundle', filename: 'release.zip', kind: 'bundle', media_type: 'application/zip', size: 100, sha256: 'a', download_url: '/bundle' },
        { id: 'schedule', filename: 'schedule.csv', kind: 'schedule', media_type: 'text/csv', size: 20, sha256: 'b', download_url: '/schedule' },
      ],
    } as ReleasePackage;
    render(<ReleasePanel plan={draftPlan} release={release} onCreate={vi.fn()} onDownload={onDownload} />);
    fireEvent.click(screen.getByRole('button', { name: 'Скачать полный пакет' }));
    fireEvent.click(screen.getByRole('button', { name: /Посадочная ведомость/ }));
    expect(onDownload.mock.calls).toEqual([['/bundle'], ['/schedule']]);
  });
});
