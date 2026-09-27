import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  Project,
  SourceObjectReviewPage,
  SourceObjectContext,
} from '@green/api-client';
import { preparationApi } from '@/features/source-preparation/api/preparationApi';
import { SourceObjectReview } from './SourceObjectReview';

const sourceSha = 'a'.repeat(64);
const project = {
  id: 'project',
  name: 'Review',
  status: 'mapped',
  map_ready: true,
  state_version: 25,
  geometry_version: 18,
  source_file: {
    content_sha256: sourceSha,
    cad_snapshot_provenance: { live_capture: {} },
  },
  layers: [
    {
      id: 'buildings',
      source_name: 'Здания',
      mapped_kind: 'building',
      entity_types: { LINE: 2 },
    },
  ],
} as unknown as Project;
const page: SourceObjectReviewPage = {
  source_sha256: sourceSha,
  total: 2,
  offset: 0,
  items: ['BB', 'CC'].map((handle, i) => ({
    route: `AA/${handle}`,
    source: { handle, instance_chain: ['AA'] },
    geometry_sha256: 'b'.repeat(64),
    interpretation: 'area',
    layer: 'Здания',
    entity_type: 'LINE',
    path: [
      [0, i],
      [10, i],
    ],
    endpoint_distance_m: 10,
  })),
};
const context: SourceObjectContext = {
  source_sha256: sourceSha,
  focus_route: 'AA/BB',
  extent: [-8, -8, 18, 18],
  total: 2,
  limited: false,
  objects: page.items.map((item) => ({
    ...item,
    paths: [item.path],
    kind: 'building',
    native_area: false,
    can_join: true,
    reviewable: true,
  })),
};
function open() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      <SourceObjectReview project={project} disabled={false} />
    </QueryClientProvider>,
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Разобрать линии без площади' }),
  );
}
describe('SourceObjectReview', () => {
  beforeEach(() => {
    vi.spyOn(preparationApi, 'getSourceObjectReview').mockResolvedValue(page);
    vi.spyOn(preparationApi, 'getSourceObjectContext').mockResolvedValue(
      context,
    );
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });
  it('saves only the selected identity and shows pending state', async () => {
    const save = vi
      .spyOn(preparationApi, 'decideSourceObject')
      .mockReturnValue(new Promise(() => {}));
    open();
    fireEvent.click(
      await screen.findByRole('button', { name: 'Это линейный элемент' }),
    );
    await screen.findByText('Сохраняем решение');
    expect(save).toHaveBeenCalledWith(
      'project',
      {
        source_sha256: sourceSha,
        source: page.items[0].source,
        geometry_sha256: page.items[0].geometry_sha256,
        interpretation: 'linear',
      },
      { expectedStateVersion: 25 },
    );
    expect(
      screen.getByRole('button', { name: 'Это обозначение' }),
    ).toBeDisabled();
    expect(
      screen.getAllByRole('button', { name: 'Закрыть' }).at(-1),
    ).toBeDisabled();
    fireEvent.click(screen.getAllByRole('button', { name: 'Закрыть' })[0]);
    expect(screen.getByRole('dialog')).toBeVisible();
  });
  it('refuses a review from another capture without writing', async () => {
    vi.mocked(preparationApi.getSourceObjectReview).mockResolvedValue({
      ...page,
      source_sha256: 'c'.repeat(64),
    });
    const save = vi.spyOn(preparationApi, 'decideSourceObject');
    open();
    fireEvent.click(
      await screen.findByRole('button', { name: 'Это обозначение' }),
    );
    await screen.findByText('Исходные данные изменились');
    expect(save).not.toHaveBeenCalled();
  });
  it('autofocuses queue and changes object with arrows without saving', async () => {
    const save = vi.spyOn(preparationApi, 'decideSourceObject');
    open();
    await screen.findByRole('button', { name: 'Это линейный элемент' });
    const list = screen.getByRole('listbox', { name: 'Линии для проверки' });
    expect(list).toHaveFocus();
    fireEvent.keyDown(list, { key: 'ArrowDown' });
    await waitFor(() =>
      expect(preparationApi.getSourceObjectContext).toHaveBeenCalledWith(
        'project',
        'AA/CC',
        1,
      ),
    );
    expect(screen.getByRole('option', { name: /CC/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(save).not.toHaveBeenCalled();
  });
  it('selects neighboring objects by keyboard and requires native validation before accepting', async () => {
    const check = vi
      .spyOn(preparationApi, 'checkSourceAreaGroup')
      .mockResolvedValue({
        valid: true,
        reason: 'AutoCAD подтвердил замкнутую область',
        detail: '',
      });
    const save = vi
      .spyOn(preparationApi, 'acceptSourceAreaGroup')
      .mockReturnValue(new Promise(() => {}));
    open();
    await screen.findByRole('button', { name: 'Это линейный элемент' });
    fireEvent.click(screen.getByRole('button', { name: 'Собрать контур' }));
    expect(
      screen.queryByRole('button', { name: 'Принять область' }),
    ).toBeNull();
    const scene = screen.getByRole('group', {
      name: 'Выбранный объект и окружение чертежа',
    });
    fireEvent.keyDown(scene, { key: 'ArrowRight' });
    fireEvent.keyDown(scene, { key: 'Enter' });
    fireEvent.click(
      screen.getByRole('button', { name: 'Проверить в AutoCAD' }),
    );
    fireEvent.click(
      await screen.findByRole('button', { name: 'Принять область' }),
    );
    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(check).toHaveBeenCalledWith('project', {
      source_sha256: sourceSha,
      kind: 'building',
      members: page.items.map((item) => ({
        source: item.source,
        geometry_sha256: item.geometry_sha256,
      })),
    });
    expect(save.mock.calls[0][2]).toEqual({ expectedStateVersion: 25 });
  });
  it('invalidates a successful preview when selection changes', async () => {
    vi.spyOn(preparationApi, 'checkSourceAreaGroup').mockResolvedValue({
      valid: true,
      reason: 'Область подтверждена',
      detail: '',
    });
    open();
    await screen.findByRole('button', { name: 'Это линейный элемент' });
    fireEvent.click(screen.getByRole('button', { name: 'Собрать контур' }));
    const scene = screen.getByRole('group', {
      name: 'Выбранный объект и окружение чертежа',
    });
    fireEvent.keyDown(scene, { key: 'ArrowRight' });
    fireEvent.keyDown(scene, { key: 'Enter' });
    fireEvent.click(
      screen.getByRole('button', { name: 'Проверить в AutoCAD' }),
    );
    await screen.findByRole('button', { name: 'Принять область' });
    fireEvent.keyDown(scene, { key: 'Enter' });
    expect(
      screen.queryByRole('button', { name: 'Принять область' }),
    ).toBeNull();
  });
});
