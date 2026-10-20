import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ComponentSettings } from './ComponentSettings';

vi.mock('@/shared/desktop/desktopBridge', () => ({
  desktopBridge: {
    canChooseDependency: true,
    chooseDependency: vi.fn(async () => 'D:\\Мои программы\\blender.exe'),
  },
}));
const paths = {
  autocad_root: '',
  autocad_template: '',
  blender: '',
  botaniq_root: '',
  pbr_root: '',
};
afterEach(() => vi.unstubAllGlobals());

it('chooses a native path, reports an invalid save and preserves the correction across reopening', async () => {
  let saved = { ...paths };
  let reject = true;
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (!init?.method)
      return new Response(JSON.stringify({ paths: saved, checks: {} }));
    const selected = JSON.parse(String(init.body));
    if (reject)
      return new Response(
        JSON.stringify({
          paths: selected,
          valid: false,
          checks: {
            blender: {
              ok: false,
              state: 'invalid',
              message: 'Blender не смог сообщить версию',
            },
          },
        }),
        { status: 422 },
      );
    saved = selected;
    return new Response(
      JSON.stringify({ paths: saved, valid: true, checks: {} }),
    );
  });
  vi.stubGlobal('fetch', fetcher);
  render(<ComponentSettings />);
  fireEvent.click(await screen.findByRole('button', { name: 'Компоненты' }));
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Выбрать: Blender' }));
  await waitFor(() =>
    expect(screen.getByLabelText('Blender')).toHaveValue(
      'D:\\Мои программы\\blender.exe',
    ),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Исправьте');
  expect(screen.getByText('Blender не смог сообщить версию')).toBeVisible();
  reject = false;
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  expect(
    await screen.findByText(
      'Настройки сохранены. Новые задания используют выбранные пути.',
    ),
  ).toBeVisible();
  fireEvent.click(
    screen.getAllByRole('button', { name: 'Закрыть' })[0],
  );
  await waitFor(() =>
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Компоненты' }));
  await waitFor(() =>
    expect(screen.getByLabelText('Blender')).toHaveValue(saved.blender),
  );
  expect(saved.blender).toBe('D:\\Мои программы\\blender.exe');
});

it('fills empty paths automatically without overwriting an unsaved manual selection', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({ paths: { ...paths, botaniq_root: 'D:\\Detected' } }),
        ),
    ),
  );
  render(<ComponentSettings />);
  fireEvent.click(await screen.findByRole('button', { name: 'Компоненты' }));
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Найти автоматически' }),
    ).toBeEnabled(),
  );
  fireEvent.change(screen.getByLabelText('Библиотека Botaniq'), {
    target: { value: 'E:\\Мой пак' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Найти автоматически' }));
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Найти автоматически' }),
    ).toBeEnabled(),
  );
  expect(screen.getByLabelText('Библиотека Botaniq')).toHaveValue(
    'E:\\Мой пак',
  );
});

it('does not expose desktop settings on a hosted page without the private endpoint', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response('not found', { status: 404 })),
  );
  const view = render(<ComponentSettings />);
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  expect(view.container).toBeEmptyDOMElement();
});
