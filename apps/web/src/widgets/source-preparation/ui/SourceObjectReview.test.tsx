import {
  cleanup,
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  Project,
  SourceObjectReviewPage,
  SourceObjectContext,
} from '@green/api-client';
import { preparationApi } from '@/features/source-preparation/api/preparationApi';
import { ProjectWorkProvider } from '@/features/project-navigation/model/ProjectWorkProvider';
import { acknowledgeProjectWrite } from '@/entities/project/model/acknowledgedRead';
import {
  canReviewSourceObjects,
  SourceObjectReview,
} from './SourceObjectReview';

const { readProject } = vi.hoisted(() => ({ readProject: vi.fn() }));
vi.mock('@green/api-client', async (original) => {
  const actual = await original<typeof import('@green/api-client')>();
  return { ...actual, api: { ...actual.api, getProjectRead: readProject } };
});

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
function open(guarded = false, snapshot = project) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const form = <SourceObjectReview project={snapshot} disabled={false} />;
  const router = guarded
    ? createMemoryRouter(
        [
          {
            path: '/projects/project/setup',
            element: (
              <ProjectWorkProvider projectId="project">
                {form}
              </ProjectWorkProvider>
            ),
          },
          { path: '/projects', element: <p>Project library</p> },
        ],
        { initialEntries: ['/projects/project/setup'] },
      )
    : undefined;
  render(
    <QueryClientProvider client={client}>
      {router ? <RouterProvider router={router} /> : form}
    </QueryClientProvider>,
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Разобрать объекты чертежа' }),
  );
  return { client, router };
}
describe('SourceObjectReview', () => {
  it('keeps live captures without a native session reviewable and hides unrelated geometry', () => {
    expect(project.source_file?.native_session).toBeUndefined();
    expect(canReviewSourceObjects(project)).toBe(true);
    expect(canReviewSourceObjects({ ...project, source_file: undefined })).toBe(
      false,
    );
    expect(
      canReviewSourceObjects({
        ...project,
        layers: project.layers!.map((layer) => ({
          ...layer,
          mapped_kind: 'existing_green',
        })),
      }),
    ).toBe(false);
    expect(
      canReviewSourceObjects({
        ...project,
        layers: project.layers!.map((layer) => ({
          ...layer,
          entity_types: { HATCH: 2 },
        })),
      }),
    ).toBe(false);
  });
  it('includes mixed layer zero with native areas and XREF-qualified zero names', () => {
    for (const source_name of ['0', 'Подоснова|0'])
      expect(
        canReviewSourceObjects({
          ...project,
          layers: project.layers!.map((layer) => ({
            ...layer,
            source_name,
            mapped_kind: 'ignore',
            entity_types: { HATCH: 2 },
          })),
        }),
      ).toBe(true);
  });
  beforeEach(() => {
    readProject.mockReset();
    const NativeRequest = Request;
    vi.stubGlobal(
      'Request',
      class extends NativeRequest {
        constructor(input: RequestInfo | URL, init?: RequestInit) {
          super(input, { ...init, signal: undefined });
        }
      },
    );
    vi.spyOn(preparationApi, 'getSourceObjectReview').mockResolvedValue(page);
    vi.spyOn(preparationApi, 'getSourceObjectContext').mockResolvedValue(
      context,
    );
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });
  it('saves a physical role only for the selected zero-layer instance and protects its draft', async () => {
    const zero = {
      ...project,
      layers: project.layers!.map((layer) => ({
        ...layer,
        source_name: '0',
        mapped_kind: 'ignore' as const,
      })),
    };
    vi.mocked(preparationApi.getSourceObjectContext).mockResolvedValue({
      ...context,
      objects: context.objects.map((item) => ({
        ...item,
        layer: '0',
        kind: 'ignore',
      })),
    });
    const save = vi
      .spyOn(preparationApi, 'decideSourceObject')
      .mockReturnValue(new Promise(() => {}));
    open(true, zero);
    const role = await screen.findByRole('combobox', {
      name: 'Назначение объекта',
    });
    expect(role).toHaveValue('');
    fireEvent.change(role, { target: { value: 'lawn' } });
    expect(screen.getByRole('button', { name: 'Применить' })).toBeEnabled();
    expect(
      screen.getByRole('combobox', { name: 'Слой для разбора объектов' }),
    ).toBeDisabled();
    expect(screen.getByRole('option', { name: /CC/ })).toBeDisabled();
    const draft = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(draft);
    expect(draft.defaultPrevented).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Применить' }));
    await screen.findByText('Сохраняем решение');
    expect(screen.getByRole('button', { name: 'Применить' })).toHaveAttribute(
      'aria-busy',
      'true',
    );
    expect(save).toHaveBeenCalledWith(
      'project',
      {
        source_sha256: sourceSha,
        source: page.items[0].source,
        geometry_sha256: page.items[0].geometry_sha256,
        interpretation: 'area',
        kind: 'lawn',
      },
      { expectedStateVersion: 25 },
    );
    expect(save).toHaveBeenCalledTimes(1);
    expect(role).toBeDisabled();
  });
  it('cancels an object-role draft without writing or blocking inspection', async () => {
    const save = vi.spyOn(preparationApi, 'decideSourceObject');
    open(true);
    const role = await screen.findByRole('combobox', {
      name: 'Назначение объекта',
    });
    fireEvent.change(role, { target: { value: 'lawn' } });
    fireEvent.click(screen.getByRole('button', { name: 'Отменить' }));
    expect(role).toHaveValue('building');
    expect(screen.queryByRole('button', { name: 'Применить' })).toBeNull();
    expect(screen.getByRole('option', { name: /CC/ })).toBeEnabled();
    const after = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(after);
    expect(after.defaultPrevented).toBe(false);
    expect(save).not.toHaveBeenCalled();
  });
  it.each(['role', 'group'] as const)(
    'shows removal progress only on its own saved group while a %s draft is present',
    async (draft) => {
      const withGroups: Project = {
        ...project,
        source_file: {
          ...project.source_file!,
          area_groups: ['old-a', 'old-b'].map((id) => ({
            id,
            source_sha256: sourceSha,
            kind: 'building',
            members: page.items.map((item) => ({
              source: item.source,
              geometry_sha256: item.geometry_sha256,
            })),
          })),
        },
      };
      const remove = vi
        .spyOn(preparationApi, 'removeSourceAreaGroup')
        .mockReturnValue(new Promise(() => {}));
      vi.spyOn(preparationApi, 'checkSourceAreaGroup').mockResolvedValue({
        valid: true,
        reason: 'Контур проверен',
        detail: '',
      });
      open(false, withGroups);
      let draftAction: HTMLElement;
      if (draft === 'role') {
        fireEvent.change(
          await screen.findByRole('combobox', { name: 'Назначение объекта' }),
          { target: { value: 'lawn' } },
        );
        draftAction = screen.getByRole('button', { name: 'Применить' });
      } else {
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
        draftAction = await screen.findByRole('button', {
          name: 'Принять область',
        });
      }
      fireEvent.click(
        screen.getByRole('button', { name: 'Подтверждённые объединения (2)' }),
      );
      const [selected, other] = screen.getAllByRole('button', {
        name: 'Отменить объединение',
      });
      fireEvent.click(selected);
      await waitFor(() => expect(remove).toHaveBeenCalledOnce());
      expect(remove).toHaveBeenCalledWith('project', 'old-a', {
        expectedStateVersion: 25,
      });
      expect(selected).toHaveAttribute('aria-busy', 'true');
      expect(selected).toBeDisabled();
      expect(other).not.toHaveAttribute('aria-busy');
      expect(other).toBeDisabled();
      expect(draftAction).not.toHaveAttribute('aria-busy');
      expect(draftAction).toBeDisabled();
      expect(
        screen
          .getAllByRole('button')
          .filter((button) => button.getAttribute('aria-busy') === 'true'),
      ).toHaveLength(1);
      fireEvent.click(other);
      expect(remove).toHaveBeenCalledOnce();
    },
  );
  it('reopens a saved object role and removes it through the existing decision action', async () => {
    const saved = {
      ...project,
      source_file: {
        ...project.source_file!,
        object_decisions: [
          {
            source: page.items[0].source,
            geometry_sha256: page.items[0].geometry_sha256,
            interpretation: 'area' as const,
            kind: 'lawn' as const,
          },
        ],
      },
    };
    const save = vi
      .spyOn(preparationApi, 'decideSourceObject')
      .mockReturnValue(new Promise(() => {}));
    open(false, saved);
    expect(
      await screen.findByRole('combobox', { name: 'Назначение объекта' }),
    ).toHaveValue('lawn');
    expect(screen.queryByRole('button', { name: 'Применить' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Отменить решение' }));
    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(save.mock.calls[0][1]).toEqual({
      source_sha256: sourceSha,
      source: page.items[0].source,
      geometry_sha256: page.items[0].geometry_sha256,
      interpretation: 'area',
    });
  });
  it('preserves a saved physical override when marking the same open curve as linear', async () => {
    const saved = {
      ...project,
      source_file: {
        ...project.source_file!,
        object_decisions: [
          {
            source: page.items[0].source,
            geometry_sha256: page.items[0].geometry_sha256,
            interpretation: 'area' as const,
            kind: 'building' as const,
          },
        ],
      },
    };
    const save = vi
      .spyOn(preparationApi, 'decideSourceObject')
      .mockReturnValue(new Promise(() => {}));
    open(false, saved);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Это линейный элемент' }),
    );
    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(save.mock.calls[0][1]).toMatchObject({
      interpretation: 'linear',
      kind: 'building',
    });
  });
  it.each(['HATCH', 'REGION', 'AcDbHatch', 'AcDbRegion', 'SOLID', '3DFACE'])(
    'offers native %s in zero a physical role while disabling the unsupported linear action',
    async (entityType) => {
      const zero = {
        ...project,
        layers: project.layers!.map((layer) => ({
          ...layer,
          source_name: '0',
          mapped_kind: 'ignore' as const,
          entity_types: { [entityType]: 2 },
        })),
      };
      vi.mocked(preparationApi.getSourceObjectContext).mockResolvedValue({
        ...context,
        objects: context.objects.map((item) => ({
          ...item,
          kind: null,
          native_area: true,
          can_join: false,
          entity_type: entityType,
        })),
      });
      open(false, zero);
      expect(
        await screen.findByRole('combobox', { name: 'Назначение объекта' }),
      ).toHaveValue('');
      expect(
        screen.getByRole('button', { name: 'Это линейный элемент' }),
      ).toBeDisabled();
      expect(
        screen.getByRole('button', { name: 'Собрать контур' }),
      ).toBeDisabled();
      expect(
        screen.getByRole('button', { name: 'Это обозначение' }),
      ).toBeEnabled();
    },
  );

  it('keeps the linear action available for a native closed polyline', async () => {
    const zero = {
      ...project,
      layers: project.layers!.map((layer) => ({
        ...layer,
        source_name: '0',
        mapped_kind: 'ignore' as const,
        entity_types: { LWPOLYLINE: 2 },
      })),
    };
    vi.mocked(preparationApi.getSourceObjectContext).mockResolvedValue({
      ...context,
      objects: context.objects.map((item) => ({
        ...item,
        kind: null,
        native_area: true,
        can_join: false,
        entity_type: 'LWPOLYLINE',
      })),
    });
    const save = vi
      .spyOn(preparationApi, 'decideSourceObject')
      .mockReturnValue(new Promise(() => {}));
    open(false, zero);
    const linear = await screen.findByRole('button', {
      name: 'Это линейный элемент',
    });
    expect(linear).toBeEnabled();
    fireEvent.click(linear);
    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(save.mock.calls[0][1]).toMatchObject({ interpretation: 'linear' });
  });
  it('recovers a lost role response by reading the exact kind without a second POST', async () => {
    const save = vi
      .spyOn(preparationApi, 'decideSourceObject')
      .mockRejectedValue(new Error('Ответ потерян'));
    const goal = {
      source: page.items[0].source,
      geometry_sha256: page.items[0].geometry_sha256,
      interpretation: 'area' as const,
      kind: 'lawn' as const,
    };
    const saved = {
      ...project,
      state_version: 26,
      source_file: { ...project.source_file!, object_decisions: [goal] },
    };
    readProject
      .mockResolvedValueOnce({
        ...saved,
        source_file: {
          ...saved.source_file,
          object_decisions: [{ ...goal, kind: 'road' }],
        },
      })
      .mockResolvedValueOnce(saved);
    const { client } = open(true);
    fireEvent.change(
      await screen.findByRole('combobox', { name: 'Назначение объекта' }),
      { target: { value: 'lawn' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Применить' }));
    await screen.findByText('Ответ потерян');
    fireEvent.click(
      screen.getByRole('button', { name: 'Проверить состояние' }),
    );
    await waitFor(() => expect(readProject).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Проверить состояние' }),
      ).toBeEnabled(),
    );
    expect(
      screen.getByRole('combobox', { name: 'Назначение объекта' }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole('button', { name: 'Проверить состояние' }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: 'Проверить состояние' }),
      ).toBeNull(),
    );
    expect(save).toHaveBeenCalledTimes(1);
    expect(client.getQueryData(['setup-project', 'project'])).toEqual(saved);
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
    expect(
      screen.getAllByRole('button', { name: 'Закрыть' })[0],
    ).toBeDisabled();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.getByRole('dialog')).toBeVisible();
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
    const list = screen.getByRole('listbox', { name: 'Объекты для проверки' });
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
  it('opens keyboard help outside the review flow and dismisses it without closing the review', async () => {
    const save = vi.spyOn(preparationApi, 'decideSourceObject');
    open();
    await screen.findByRole('button', { name: 'Это линейный элемент' });
    expect(screen.queryByText('Выбрать объект в очереди')).toBeNull();
    fireEvent.click(
      screen.getByRole('button', { name: 'Управление с клавиатуры' }),
    );
    expect(await screen.findByText('Выбрать объект в очереди')).toBeVisible();
    fireEvent.keyDown(
      screen.getByRole('dialog', { name: 'Управление с клавиатуры' }),
      { key: 'Escape' },
    );
    await waitFor(() =>
      expect(screen.queryByText('Выбрать объект в очереди')).toBeNull(),
    );
    expect(
      screen.getByRole('dialog', { name: 'Проверка геометрии' }),
    ).toBeVisible();
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
    expect(
      screen.getByRole('button', { name: 'Принять область' }),
    ).toHaveAttribute('aria-busy', 'true');
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
  it('protects a selected contour through route exit, modal close and layer changes until explicit cancellation', async () => {
    const { router } = open(true);
    await screen.findByRole('button', { name: 'Это линейный элемент' });
    fireEvent.click(screen.getByRole('button', { name: 'Собрать контур' }));
    expect(
      screen.getAllByRole('button', { name: 'Закрыть' }).at(-1),
    ).toBeDisabled();
    expect(
      screen.getAllByRole('button', { name: 'Закрыть' })[0],
    ).toBeDisabled();
    expect(
      screen.getByRole('combobox', { name: 'Слой для разбора объектов' }),
    ).toBeDisabled();
    const before = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(before);
    expect(before.defaultPrevented).toBe(true);
    await act(async () => router!.navigate('/projects'));
    expect(router!.state.location.pathname).toBe('/projects/project/setup');
    expect(
      screen.getByRole('dialog', { name: 'Уйти без применения?' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Остаться' }));
    fireEvent.click(screen.getByRole('button', { name: 'Завершить выбор' }));
    for (const close of screen.getAllByRole('button', { name: 'Закрыть' }))
      expect(close).not.toBeDisabled();
    const after = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(after);
    expect(after.defaultPrevented).toBe(false);
    await act(async () => router!.navigate('/projects'));
    expect(router!.state.location.pathname).toBe('/projects');
  });
  it('does not treat inspecting the queue as an unsaved draft', async () => {
    const { router } = open(true);
    await screen.findByRole('button', { name: 'Это линейный элемент' });
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
    await act(async () => router!.navigate('/projects'));
    expect(router!.state.location.pathname).toBe('/projects');
  });
  it('reads a lost decision without repeating POST and refuses old or mismatching source metadata', async () => {
    const save = vi
      .spyOn(preparationApi, 'decideSourceObject')
      .mockRejectedValue(new Error('Ответ потерян'));
    const goal = {
      source: page.items[0].source,
      geometry_sha256: page.items[0].geometry_sha256,
      interpretation: 'linear' as const,
    };
    const fresh = {
      ...project,
      state_version: 26,
      source_file: { ...project.source_file!, object_decisions: [goal] },
    };
    const read = readProject
      .mockResolvedValueOnce({ ...fresh, state_version: 24 })
      .mockResolvedValueOnce({
        ...fresh,
        source_file: { ...fresh.source_file, object_decisions: [] },
      })
      .mockResolvedValueOnce(fresh);
    const { client, router } = open(true);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Это линейный элемент' }),
    );
    await screen.findByText('Ответ потерян');
    expect(
      screen.getByRole('button', { name: 'Это линейный элемент' }),
    ).toBeDisabled();
    expect(
      screen.getAllByRole('button', { name: 'Закрыть' }).at(-1),
    ).toBeDisabled();
    await act(async () => router!.navigate('/projects'));
    expect(
      screen.getByRole('dialog', { name: 'Проверьте результат сохранения' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Остаться' }));
    for (let i = 0; i < 2; i++) {
      fireEvent.click(
        screen.getByRole('button', { name: 'Проверить состояние' }),
      );
      await waitFor(() => expect(read).toHaveBeenCalledTimes(i + 1));
      await waitFor(() =>
        expect(
          screen.getByRole('button', { name: 'Проверить состояние' }),
        ).not.toBeDisabled(),
      );
      expect(
        screen.getByRole('button', { name: 'Это линейный элемент' }),
      ).toBeDisabled();
    }
    fireEvent.click(
      screen.getByRole('button', { name: 'Проверить состояние' }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: 'Проверить состояние' }),
      ).toBeNull(),
    );
    expect(save).toHaveBeenCalledTimes(1);
    expect(read).toHaveBeenCalledWith('project', false);
    expect(client.getQueryData(['setup-project', 'project'])).toEqual(fresh);
    await act(async () => router!.navigate('/projects'));
    expect(router!.state.location.pathname).toBe('/projects');
  });
  it('does not resend an already saved interpretation while the review queue still shows its default', async () => {
    const goal = {
      source: page.items[0].source,
      geometry_sha256: page.items[0].geometry_sha256,
      interpretation: 'linear' as const,
    };
    const current = {
      ...project,
      source_file: { ...project.source_file!, object_decisions: [goal] },
    };
    const save = vi.spyOn(preparationApi, 'decideSourceObject');
    open(false, current);
    const button = await screen.findByRole('button', {
      name: 'Это линейный элемент',
    });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(save).not.toHaveBeenCalled();
  });
  it('allows leaving a confirmed group save when a newer observed revision prevents publishing its response', async () => {
    vi.spyOn(preparationApi, 'checkSourceAreaGroup').mockResolvedValue({
      valid: true,
      reason: 'Контур проверен',
      detail: '',
    });
    const saved = {
      ...project,
      state_version: 26,
      source_file: {
        ...project.source_file!,
        area_groups: [
          {
            id: 'group',
            source_sha256: sourceSha,
            kind: 'building' as const,
            members: page.items.map((item) => ({
              source: item.source,
              geometry_sha256: item.geometry_sha256,
            })),
          },
        ],
      },
    };
    const save = vi
      .spyOn(preparationApi, 'acceptSourceAreaGroup')
      .mockResolvedValue(saved);
    const { client, router } = open(true);
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
    const accept = await screen.findByRole('button', {
      name: 'Принять область',
    });
    // A response for revision 26 can arrive after another write's revision 30
    // has already been observed. Its commit remains confirmed, its DTO stale.
    acknowledgeProjectWrite(client, 'project', { stateVersion: 30 });
    fireEvent.click(accept);
    await screen.findByText('Решение сохранено, обновите данные');
    expect(
      screen.getByRole('button', { name: 'Принять область' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('combobox', { name: 'Слой для разбора объектов' }),
    ).toBeDisabled();
    expect(
      screen.getAllByRole('button', { name: 'Закрыть' }).at(-1),
    ).not.toBeDisabled();
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
    await act(async () => router!.navigate('/projects'));
    expect(router!.state.location.pathname).toBe('/projects');
    expect(save).toHaveBeenCalledTimes(1);
  });
});
