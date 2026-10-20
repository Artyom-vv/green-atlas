import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DataPassport } from '@green/api-client';
import { preparationApi } from '@/features/source-preparation/api/preparationApi';
import { WorkspaceGeometryNotice } from './WorkspaceGeometryNotice';

const passport: DataPassport = {
  overall_status: 'limited',
  calculation_status: 'ready',
  mass_placement_status: 'limited',
  summary: 'Есть пропуски',
  incomplete_layers: ['0', 'Проектные решения|Тротуар'],
};

function openProject(projectId = 'street', prepared = true) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <WorkspaceGeometryNotice
        key={projectId}
        projectId={projectId}
        prepared={prepared}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(preparationApi, 'getDataPassport').mockResolvedValue(passport);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('уведомление о подготовленной геометрии', () => {
  it('показывает уведомление нового проекта при переходе в том же рабочем окне', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const projectView = (projectId: string) => (
      <QueryClientProvider client={client}>
        <WorkspaceGeometryNotice
          key={projectId}
          projectId={projectId}
          prepared
        />
      </QueryClientProvider>
    );
    const user = userEvent.setup();
    const view = render(projectView('first'));
    expect(await screen.findByRole('dialog')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Понятно' }));
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
    );
    view.rerender(projectView('second'));
    expect(await screen.findByRole('dialog')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Понятно' }));
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
    );
    view.rerender(projectView('first'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(preparationApi.getDataPassport).toHaveBeenCalledTimes(2);
  });

  it('показывает слои, закрывается без соглашения и не повторяется после открытия проекта', async () => {
    const accept = vi.spyOn(preparationApi, 'acceptPartialGeometry');
    const user = userEvent.setup();
    const view = openProject();
    expect(
      await screen.findByRole('dialog', { name: 'Есть пропуски геометрии' }),
    ).toBeVisible();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    await user.click(
      screen.getByRole('button', { name: 'Слои с пропусками (2)' }),
    );
    expect(screen.getByText('Проектные решения|Тротуар')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Понятно' }));
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
    );
    expect(accept).not.toHaveBeenCalled();
    view.unmount();
    openProject();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(preparationApi.getDataPassport).toHaveBeenCalledTimes(1);
  });

  it('сохраняет просмотр при закрытии с клавиатуры и отдельно для каждого проекта', async () => {
    const user = userEvent.setup();
    const view = openProject('first');
    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(dialog).toHaveFocus());
    await user.keyboard('{Escape}');
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
    );
    view.unmount();
    const reopened = openProject('first');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    reopened.unmount();
    openProject('second');
    expect(await screen.findByRole('dialog')).toBeVisible();
  });

  it('не предупреждает до подготовки и не показывает общую плашку из-за одного CRS', async () => {
    const view = openProject('street', false);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(preparationApi.getDataPassport).not.toHaveBeenCalled();
    view.unmount();
    vi.mocked(preparationApi.getDataPassport).mockResolvedValue({
      ...passport,
      incomplete_layers: [],
      gaps: ['Система координат не подтверждена'],
    });
    openProject();
    await waitFor(() =>
      expect(preparationApi.getDataPassport).toHaveBeenCalledTimes(1),
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('закрывается, когда локальная настройка недоступна', async () => {
    const user = userEvent.setup();
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage unavailable');
    });
    openProject();
    expect(await screen.findByRole('dialog')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Закрыть' }));
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
    );
  });
});
