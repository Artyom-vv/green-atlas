import { useState } from 'react';
import type { PropsWithChildren } from 'react';
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, type Plan, type ReleasePackage } from '@green/api-client';
import { Dialog } from '@green/ui';
import { ReleasePanel } from '../ui/ReleasePanel';
import { useProjectRelease } from './useProjectRelease';
import { emptyReleaseForm } from './releaseDraft';
import {
  releaseDraftKey,
  resetReleaseDraftMemory,
} from './releaseDraftStorage';

const plan = {
  id: 'plan-1',
  version: 7,
  objects: [
    {
      id: 'tree-1',
      species_revision_id: 'tilia-1',
      kind: 'tree',
      x: 0,
      y: 0,
      radius: 2,
      size_class: 'standard',
      spacing_policy: 'balanced',
      locked: false,
      status: 'valid',
    },
  ],
  issues: [],
} as Plan;
const options = { plan, geometryVersion: 3, initialHorizon: 20 };
const notes = {
  ...emptyReleaseForm(30),
  mode: 'final' as const,
  basis: {
    pp616_status: 'documented' as const,
    pp616_reference: '  Документ «616»\nДополнение пользователя: проверить  ',
    pp1160_status: 'not_required' as const,
    pp1160_reference: 'Сохранить заметку 1160',
    confirmed_by: 'Иванов И. И.',
  },
};
const packageResult = (
  id = 'release-1',
  projectId = 'project-1',
): ReleasePackage => ({
  id,
  project_id: projectId,
  plan_version: 7,
  geometry_version: 3,
  mode: 'final',
  status: 'ready',
  scene_horizon: 30,
  created_at: '2026-09-10T12:00:00Z',
  rule_set_revision: 'rules',
  species_catalog_revision: 'catalog',
  artifacts: [],
});
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}
function wrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}
beforeEach(() => {
  resetReleaseDraftMemory();
  sessionStorage.clear();
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('release draft lifecycle', () => {
  it('preserves literal form fields and the chosen horizon through Dialog close/reopen and files/form navigation', async () => {
    localStorage.setItem('green-atlas:release:project-1', 'old-release');
    vi.spyOn(api, 'getRelease').mockResolvedValue(packageResult('old-release'));
    const create = vi.spyOn(api, 'createRelease');
    const mapHorizon = vi.fn();
    function View() {
      const release = useProjectRelease('project-1', options);
      const [open, setOpen] = useState(true);
      return (
        <>
          <button onClick={() => setOpen(true)}>Открыть выпуск</button>
          <Dialog open={open} title="Выпуск" onClose={() => setOpen(false)}>
            {release.restoring ? (
              <p>Загрузка</p>
            ) : (
              <ReleasePanel
                plan={plan}
                geometryVersion={3}
                release={release.release}
                formMethods={release.formMethods}
                formOpen={release.formOpen}
                hasDraft={release.hasDraft}
                onOpenForm={release.openForm}
                onShowFiles={release.showFiles}
                onClearDraft={release.clearDraft}
                onReviewContext={release.reviewContext}
                onCreate={release.createRelease.mutate}
                onDownload={vi.fn()}
                onGrowthHorizon={mapHorizon}
              />
            )}
          </Dialog>
        </>
      );
    }
    render(<View />, { wrapper: wrapper() });
    fireEvent.click(
      await screen.findByRole('button', { name: 'Собрать новый пакет' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Финальный' }));
    fireEvent.change(screen.getByLabelText('Основание решения по ПП-616'), {
      target: { value: '  Текст «616»  ' },
    });
    fireEvent.change(screen.getByLabelText('Основание решения по ПП-1160'), {
      target: { value: 'Дополнение пользователя: оставить' },
    });
    fireEvent.click(screen.getByText('Прогноз в пакете', { exact: true }));
    fireEvent.input(screen.getByRole('slider'), { target: { value: '30' } });
    expect(mapHorizon).toHaveBeenCalledExactlyOnceWith(30);
    fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
    expect(
      screen.queryByLabelText('Основание решения по ПП-616'),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Открыть выпуск' }));
    expect(screen.getByLabelText('Основание решения по ПП-616')).toHaveValue(
      '  Текст «616»  ',
    );
    expect(screen.getByLabelText('Основание решения по ПП-1160')).toHaveValue(
      'Дополнение пользователя: оставить',
    );
    expect(screen.getByRole('slider', { hidden: true })).toHaveValue('30');
    fireEvent.click(
      screen.getByRole('button', { name: 'Вернуться к файлам пакета' }),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Продолжить черновик' }),
    );
    expect(screen.getByLabelText('Основание решения по ПП-616')).toHaveValue(
      '  Текст «616»  ',
    );
    expect(mapHorizon).toHaveBeenCalledTimes(1);
    expect(create).not.toHaveBeenCalled();
    expect(
      screen.getByLabelText('Основание решения по ПП-616'),
    ).toHaveAttribute('maxlength', '240');
    expect(screen.getByLabelText('Ответственный за проверку')).toHaveAttribute(
      'maxlength',
      '160',
    );
  });

  it('restores notes across real reload and isolates project A→B→A without replacing the saved horizon with a new seed', () => {
    const first = renderHook(() => useProjectRelease('project-1', options), {
      wrapper: wrapper(),
    });
    act(() => first.result.current.updateForm(notes));
    first.unmount();
    resetReleaseDraftMemory();
    const restored = renderHook(
      ({ projectId }) =>
        useProjectRelease(projectId, { ...options, initialHorizon: 0 }),
      { initialProps: { projectId: 'project-1' }, wrapper: wrapper() },
    );
    expect(restored.result.current.form).toMatchObject(notes);
    restored.rerender({ projectId: 'project-2' });
    expect(restored.result.current.form.basis.pp616_reference).toBe('');
    act(() =>
      restored.result.current.updateForm({
        ...notes,
        basis: { ...notes.basis, pp616_reference: 'Проект 2' },
      }),
    );
    restored.rerender({ projectId: 'project-1' });
    expect(restored.result.current.form).toMatchObject(notes);
    act(() => restored.result.current.clearDraft());
    expect(sessionStorage.getItem(releaseDraftKey('project-1'))).toBeNull();
    restored.unmount();
    resetReleaseDraftMemory();
    const clean = renderHook(() => useProjectRelease('project-1', options), {
      wrapper: wrapper(),
    });
    expect(clean.result.current.hasDraft).toBe(false);
    expect(clean.result.current.form.basis.pp616_reference).toBe('');
  });

  it.each(['geometry', 'plan-version', 'plan-id'] as const)(
    'retains notes and blocks final release until explicit review after a changed %s',
    (kind) => {
      const create = vi
        .spyOn(api, 'createRelease')
        .mockImplementation(() => new Promise(() => {}));
      const { result, rerender } = renderHook(
        (value) => useProjectRelease('project-1', value),
        { initialProps: options, wrapper: wrapper() },
      );
      act(() => result.current.updateForm(notes));
      rerender({
        ...options,
        geometryVersion: kind === 'geometry' ? 4 : 3,
        plan: {
          ...plan,
          version: kind === 'plan-version' ? 8 : 7,
          id: kind === 'plan-id' ? 'another-plan' : plan.id,
        },
      });
      expect(result.current.stale).toBe(true);
      expect(result.current.form).toMatchObject(notes);
      act(() => result.current.createRelease.mutate());
      expect(create).not.toHaveBeenCalled();
      act(() => result.current.reviewContext());
      expect(result.current.stale).toBe(false);
      act(() => result.current.createRelease.mutate());
      expect(create).toHaveBeenCalledExactlyOnceWith('project-1', {
        mode: 'final',
        scene_horizon: 30,
        regulatory_basis: notes.basis,
      });
    },
  );

  it('shares one in-flight submission across remounts and consumes only the matching successful draft', async () => {
    const pending = deferred<ReleasePackage>();
    const create = vi
      .spyOn(api, 'createRelease')
      .mockReturnValue(pending.promise);
    const provide = wrapper();
    const first = renderHook(() => useProjectRelease('project-1', options), {
      wrapper: provide,
    });
    act(() => {
      first.result.current.updateForm(notes);
      first.result.current.createRelease.mutate();
      first.result.current.createRelease.mutate();
    });
    expect(create).toHaveBeenCalledTimes(1);
    first.unmount();
    const returned = renderHook(() => useProjectRelease('project-1', options), {
      wrapper: provide,
    });
    expect(returned.result.current.createRelease.isPending).toBe(true);
    act(() => returned.result.current.createRelease.mutate());
    expect(create).toHaveBeenCalledTimes(1);
    await act(async () => pending.resolve(packageResult()));
    await waitFor(() =>
      expect(returned.result.current.release?.id).toBe('release-1'),
    );
    expect(returned.result.current.hasDraft).toBe(false);
    expect(returned.result.current.formOpen).toBe(false);
    expect(sessionStorage.getItem(releaseDraftKey('project-1'))).toBeNull();
    act(() => returned.result.current.openForm());
    expect(returned.result.current.form.basis.pp616_reference).toBe('');
  });

  it('keeps literal notes after failure, and clear prevents their resurrection on reopen', async () => {
    vi.spyOn(api, 'createRelease').mockRejectedValue(
      new Error('Сеть недоступна'),
    );
    const { result } = renderHook(
      () => useProjectRelease('project-1', options),
      { wrapper: wrapper() },
    );
    act(() => {
      result.current.updateForm(notes);
      result.current.createRelease.mutate();
    });
    await waitFor(() =>
      expect(result.current.createRelease.error?.message).toBe(
        'Сеть недоступна',
      ),
    );
    expect(result.current.form).toMatchObject(notes);
    act(() => result.current.clearDraft());
    act(() => result.current.openForm());
    expect(result.current.form.basis.pp616_reference).toBe('');
    expect(result.current.createRelease.error).toBeNull();
  });

  it('retains hidden final-release notes after a successful draft package and consumes them only in a successful final package', async () => {
    const create = vi
      .spyOn(api, 'createRelease')
      .mockResolvedValueOnce({
        ...packageResult('draft-package'),
        mode: 'draft',
        status: 'draft',
      })
      .mockResolvedValueOnce(packageResult('final-package'));
    const { result } = renderHook(
      () => useProjectRelease('project-1', options),
      { wrapper: wrapper() },
    );
    act(() => result.current.updateForm(notes));
    act(() => result.current.updateForm({ ...notes, mode: 'draft' }));
    act(() => result.current.createRelease.mutate());
    await waitFor(() =>
      expect(result.current.release?.id).toBe('draft-package'),
    );
    expect(create.mock.calls[0]).toEqual([
      'project-1',
      { mode: 'draft', scene_horizon: 30 },
    ]);
    expect(result.current.formOpen).toBe(false);
    expect(result.current.hasDraft).toBe(true);
    expect(result.current.form.basis).toEqual(notes.basis);
    expect(
      JSON.parse(sessionStorage.getItem(releaseDraftKey('project-1'))!).draft
        .basis,
    ).toEqual(notes.basis);
    act(() => result.current.openForm());
    expect(result.current.form.basis).toEqual(notes.basis);
    act(() => result.current.updateForm({ ...notes, mode: 'final' }));
    act(() => result.current.createRelease.mutate());
    await waitFor(() =>
      expect(result.current.release?.id).toBe('final-package'),
    );
    expect(create.mock.calls[1]).toEqual([
      'project-1',
      { mode: 'final', scene_horizon: 30, regulatory_basis: notes.basis },
    ]);
    expect(result.current.hasDraft).toBe(false);
    expect(sessionStorage.getItem(releaseDraftKey('project-1'))).toBeNull();
  });

  it.each(['project-changed', 'server-version-changed'] as const)(
    'retains the draft when %s during a successful build',
    async (reason) => {
      const pending = deferred<ReleasePackage>();
      vi.spyOn(api, 'createRelease').mockReturnValue(pending.promise);
      const { result, rerender } = renderHook(
        (value) => useProjectRelease('project-1', value),
        { initialProps: options, wrapper: wrapper() },
      );
      act(() => {
        result.current.updateForm(notes);
        result.current.createRelease.mutate();
      });
      if (reason === 'project-changed')
        rerender({ ...options, geometryVersion: 4 });
      await act(async () =>
        pending.resolve({
          ...packageResult(),
          geometry_version: reason === 'server-version-changed' ? 4 : 3,
        }),
      );
      expect(result.current.form).toMatchObject(notes);
      expect(result.current.hasDraft).toBe(true);
      expect(result.current.formOpen).toBe(true);
    },
  );

  it.each(['success', 'failure'] as const)(
    'an older %s after reload/clear/new submission cannot replace the new draft or package',
    async (outcome) => {
      const old = deferred<ReleasePackage>(),
        fresh = deferred<ReleasePackage>();
      const create = vi
        .spyOn(api, 'createRelease')
        .mockReturnValueOnce(old.promise)
        .mockReturnValueOnce(fresh.promise);
      const provide = wrapper();
      const original = renderHook(
        () => useProjectRelease('project-1', options),
        { wrapper: provide },
      );
      act(() => {
        original.result.current.updateForm(notes);
        original.result.current.createRelease.mutate();
      });
      original.unmount();
      resetReleaseDraftMemory();
      const restored = renderHook(
        () => useProjectRelease('project-1', options),
        { wrapper: provide },
      );
      expect(restored.result.current.submissionUnknown).toBe(true);
      expect(restored.result.current.form).toMatchObject(notes);
      expect(create).toHaveBeenCalledTimes(1);
      act(() => restored.result.current.clearDraft());
      act(() =>
        restored.result.current.updateForm({
          ...notes,
          basis: { ...notes.basis, pp616_reference: 'Новые заметки' },
        }),
      );
      act(() => restored.result.current.createRelease.mutate());
      await act(async () => fresh.resolve(packageResult('new-package')));
      act(() => restored.result.current.openForm());
      act(() =>
        restored.result.current.updateForm({
          ...notes,
          basis: {
            ...notes.basis,
            pp616_reference: 'Ещё более новый черновик',
          },
        }),
      );
      await act(async () =>
        outcome === 'success'
          ? old.resolve(packageResult('old-package'))
          : old.reject(new Error('Поздняя ошибка')),
      );
      expect(restored.result.current.form.basis.pp616_reference).toBe(
        'Ещё более новый черновик',
      );
      expect(restored.result.current.release?.id).toBe('new-package');
      expect(restored.result.current.createRelease.error).toBeNull();
      expect(localStorage.getItem('green-atlas:release:project-1')).toBe(
        'new-package',
      );
    },
  );
});
