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
  NativeFaceReview as Review,
  Project,
  SourceObjectContext,
} from '@green/api-client';
import { preparationApi } from '@/features/source-preparation/api/preparationApi';
import { NativeFaceReview } from './NativeFaceReview';

const { readProject } = vi.hoisted(() => ({ readProject: vi.fn() }));
vi.mock('@green/api-client', async (original) => {
  const actual = await original<typeof import('@green/api-client')>();
  return { ...actual, api: { ...actual.api, getProjectRead: readProject } };
});

const sha = 'a'.repeat(64);
const project = {
  id: 'test',
  state_version: 2,
  geometry_version: 3,
  source_file: {
    content_sha256: sha,
    native_session: { session_id: 'a'.repeat(32) },
  },
  import_status: { editability: 'editable' },
} as unknown as Project;
const review: Review = {
  source_sha256: sha,
  items: [1, 2].map((i) => ({
    key: String(i).repeat(64),
    anchor: String(i),
    layer: 'Здания',
    area_m2: 100 * i,
    member_count: 3,
    repair_count: 1,
    status: 'active',
    path: [
      [0, 0],
      [10, 0],
      [10, 10],
      [0, 0],
    ],
    repairs: [
      [
        [0, 0],
        [0.01, 0],
      ],
    ],
  })),
};
const context: SourceObjectContext = {
  source_sha256: sha,
  focus_route: '1',
  extent: [-5, -5, 15, 15],
  objects: [],
  total: 0,
  limited: false,
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
      <NativeFaceReview project={project} />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Собранные области' }));
}
describe('NativeFaceReview', () => {
  beforeEach(() => {
    readProject.mockReset();
    vi.spyOn(preparationApi, 'getNativeFaces').mockResolvedValue(review);
    vi.spyOn(preparationApi, 'getSourceObjectContext').mockResolvedValue(
      context,
    );
    vi.spyOn(preparationApi, 'decideNativeFace').mockResolvedValue(project);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });
  it('shows the derived area with context and keyboard navigation', async () => {
    open();
    const scene = await screen.findByRole('group', {
      name: 'Выбранный объект и окружение чертежа',
    });
    expect(screen.getByText('1 / 2')).toBeInTheDocument();
    fireEvent.keyDown(scene, { key: 'ArrowDown' });
    expect(screen.getByText('2 / 2')).toBeInTheDocument();
    await waitFor(() =>
      expect(preparationApi.getSourceObjectContext).toHaveBeenCalledWith(
        'test',
        `native-face:${'1'.repeat(64)}`,
        2,
      ),
    );
  });
  it('revokes only the selected face and shows saving state', async () => {
    let complete!: (value: Project) => void;
    vi.mocked(preparationApi.decideNativeFace).mockImplementation(
      () =>
        new Promise((resolve) => {
          complete = resolve;
        }),
    );
    open();
    await screen.findByText('1 / 2');
    fireEvent.click(screen.getByRole('button', { name: 'Отменить область' }));
    await screen.findByText('Сохраняем решение');
    expect(preparationApi.decideNativeFace).toHaveBeenCalledWith(
      'test',
      { source_sha256: sha, key: '2'.repeat(64), rejected: true },
      { expectedStateVersion: 2 },
    );
    expect(screen.getByRole('button', { name: 'Далее' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Закрыть' })).toBeDisabled();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.getByRole('dialog')).toBeVisible();
    complete(project);
    await waitFor(() =>
      expect(screen.queryByText('Сохраняем решение')).not.toBeInTheDocument(),
    );
  });
  it('keeps a lost toggle bound to its original face and verifies metadata without another POST', async () => {
    vi.mocked(preparationApi.decideNativeFace).mockRejectedValue(
      new Error('Ответ потерян'),
    );
    const goal = {
      ...project,
      state_version: 3,
      source_file: {
        ...project.source_file!,
        rejected_native_face_keys: ['2'.repeat(64)],
      },
    };
    const read = readProject
      .mockResolvedValueOnce({
        ...goal,
        source_file: { ...goal.source_file, rejected_native_face_keys: [] },
      })
      .mockResolvedValueOnce(goal);
    open();
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Отменить область' }),
      ).not.toBeDisabled(),
    );
    fireEvent.click(
      await screen.findByRole('button', { name: 'Отменить область' }),
    );
    await screen.findByText('Ответ потерян');
    expect(
      screen.getByRole('button', { name: 'Отменить область' }),
    ).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Далее' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Закрыть' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
    expect(screen.getByRole('dialog')).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Проверить состояние' }),
    );
    await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Проверить состояние' }),
      ).not.toBeDisabled(),
    );
    expect(
      screen.getByRole('button', { name: 'Отменить область' }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole('button', { name: 'Проверить состояние' }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: 'Проверить состояние' }),
      ).toBeNull(),
    );
    expect(preparationApi.decideNativeFace).toHaveBeenCalledTimes(1);
    expect(read).toHaveBeenCalledWith('test', false);
    fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });
  it('allows closing a read-only review load without reporting unsaved work', async () => {
    vi.mocked(preparationApi.getNativeFaces).mockReturnValue(
      new Promise(() => {}),
    );
    open();
    expect(
      screen.getByText('Подготавливаем области в AutoCAD'),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(preparationApi.decideNativeFace).not.toHaveBeenCalled();
  });
});
