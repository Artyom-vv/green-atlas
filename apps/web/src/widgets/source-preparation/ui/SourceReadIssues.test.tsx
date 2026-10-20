import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { preparationApi } from '@/features/source-preparation/api/preparationApi';
import { SourceReadIssues } from './SourceReadIssues';

function renderIssues() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <SourceReadIssues projectId="project" sourceSha="capture" />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('SourceReadIssues', () => {
  it('reads on demand and retains the address and cause behind the compact report', async () => {
    const read = vi
      .spyOn(preparationApi, 'getSourceReadIssues')
      .mockResolvedValue({
        source_sha256: 'capture',
        items: [
          {
            route: 'xref/archive/instance/7A41',
            layer: 'Подоснова|Инженерные сети',
            entity_type: 'INSERT',
            reason: 'Ссылка недоступна',
            detail: 'AutoCAD не восстановил внешний источник',
          },
        ],
      });
    renderIssues();
    expect(read).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole('button', { name: 'Объекты без расчётной геометрии' }),
    );
    await screen.findByText('Ссылка недоступна');
    expect(read).toHaveBeenCalledOnce();
    expect(screen.getByText('xref/archive/instance/7A41')).toBeVisible();
    expect(screen.getByText('Подоснова|Инженерные сети')).toBeVisible();
    expect(
      screen.queryByText('AutoCAD не восстановил внешний источник'),
    ).not.toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Ответ AutoCAD' }));
    expect(
      screen.getByText('AutoCAD не восстановил внешний источник'),
    ).toBeVisible();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(read).toHaveBeenCalledOnce();
  });

  it('can retry a read error without making a source decision', async () => {
    const read = vi
      .spyOn(preparationApi, 'getSourceReadIssues')
      .mockRejectedValueOnce(new Error('Недоступен отчёт'))
      .mockResolvedValue({ source_sha256: 'capture', items: [] });
    renderIssues();
    fireEvent.click(
      screen.getByRole('button', { name: 'Объекты без расчётной геометрии' }),
    );
    await screen.findByText('Отчёт не загрузился');
    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
    await screen.findByText('Непрочитанных объектов в отчёте нет');
    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
