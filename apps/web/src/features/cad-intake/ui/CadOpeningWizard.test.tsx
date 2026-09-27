import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ComponentProps } from 'react';
import { CadOpeningWizard } from './CadOpeningWizard';

afterEach(cleanup);
type Props = ComponentProps<typeof CadOpeningWizard>;
function props(): Props {
  return {
    open: true,
    onClose: vi.fn(),
    onReplace: vi.fn(),
    onRetry: vi.fn(),
    onMapping: vi.fn(),
    intake: {
      id: 'intake',
      project_id: 'project',
      kind: 'inspect_cad_package',
      status: 'completed',
      cad_intake: {
        passport: {
          root_id: 'test',
          entry: 'main.dxf',
          entries: ['main.dxf', 'bad.dxf'],
          manifest_sha256: 'a'.repeat(64),
          drawings: [
            {
              path: 'main.dxf',
              status: 'readable',
              inspection: { native_unresolved: 7 },
            },
            { path: 'bad.dxf', status: 'rejected' },
          ],
          references: [
            {
              owner: 'main.dxf',
              block: 'NETWORK',
              requested_path: 'network.dwg',
              status: 'missing',
            },
          ],
        },
      },
    } as Props['intake'],
    prepared: {
      loading: false,
      busy: false,
      opening: false,
      cancelling: false,
      error: null,
      launch: vi.fn(),
      open: vi.fn(),
      cancel: vi.fn(),
      refresh: vi.fn(),
      operation: null,
    } as Props['prepared'],
  };
}

it('uses the continue actions as explicit acceptance of incomplete data', () => {
  const value = props();
  render(<CadOpeningWizard {...value} />);
  fireEvent.click(screen.getByRole('button', { name: 'Далее' }));
  fireEvent.click(
    screen.getByRole('button', { name: 'Продолжить с доступными данными' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Открыть проект' }));
  expect(value.prepared.launch).toHaveBeenCalledWith({
    intake_operation_id: 'intake',
    manifest_sha256: 'a'.repeat(64),
    profile_version: 1,
    opening_review: {
      skipped_drawings: ['bad.dxf'],
      skipped_references: [{ owner: 'main.dxf', block: 'NETWORK' }],
      accept_partial_geometry: true,
    },
  });
});

it('offers repairing the package without silently accepting omissions', () => {
  const value = props();
  render(<CadOpeningWizard {...value} />);
  fireEvent.click(screen.getByRole('button', { name: 'Обновить комплект' }));
  expect(value.onReplace).toHaveBeenCalledOnce();
  expect(value.prepared.launch).not.toHaveBeenCalled();
});

it('keeps prior choices when going back, without automatically launching', () => {
  const value = props();
  render(<CadOpeningWizard {...value} />);
  fireEvent.click(screen.getByRole('button', { name: 'Далее' }));
  fireEvent.click(screen.getByRole('button', { name: 'Назад' }));
  expect(screen.getByRole('heading', { name: 'Не все файлы прочитаны' })).toBeVisible();
  expect(value.prepared.launch).not.toHaveBeenCalled();
});
