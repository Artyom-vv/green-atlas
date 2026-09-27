import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PlanningBrief } from '@green/api-client';
import { useRecommendationForm } from './useRecommendationForm';
import { useRecommendationTask } from './useRecommendationTask';

afterEach(cleanup);
const interpreted: PlanningBrief = {
  arrangement: 'area',
  profile: 'shade',
  max_sites: 12,
  unsupported: [],
  questions: [],
};
function deferred() {
  let resolve!: (value: PlanningBrief) => void;
  const promise = new Promise<PlanningBrief>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
describe('recommendation task request ownership', () => {
  it.each(['inactive', 'edit', 'manual', 'back', 'unmount'] as const)(
    'aborts on %s and ignores completion even if the interpreter ignores its signal',
    async (reason) => {
      const request = deferred();
      const onInterpret = vi.fn(
        (_task: string, _signal: AbortSignal) => request.promise,
      );
      const view = renderHook(
        ({ active }) => {
          const form = useRecommendationForm({ task: 'Больше тени' });
          const task = useRecommendationTask({
            form,
            active,
            onInterpret,
            supportsBuildingScreen: false,
          });
          return { form, task };
        },
        { initialProps: { active: true } },
      );
      const form = view.result.current.form;
      let work!: Promise<void>;
      act(() => {
        work = view.result.current.task.interpret();
      });
      expect(view.result.current.task.interpreting).toBe(true);
      if (reason === 'inactive') view.rerender({ active: false });
      else if (reason === 'edit')
        act(() => {
          form.setValue('task', 'Совсем другая задача');
        });
      else if (reason === 'manual')
        act(() => {
          view.result.current.task.manualEntry();
        });
      else if (reason === 'back')
        act(() => {
          view.result.current.task.returnToText();
        });
      else view.unmount();
      expect(onInterpret.mock.calls[0][1].aborted).toBe(true);
      await act(async () => {
        request.resolve(interpreted);
        await work;
      });
      expect(form.getValues('profile')).toBe('balanced');
      expect(form.getValues('maxSites')).toBe(40);
      if (reason !== 'unmount')
        expect(view.result.current.task.interpreting).toBe(false);
    },
  );
  it('lets a second request win without a late first response replacing its parameters', async () => {
    const first = deferred();
    const second = deferred();
    const onInterpret = vi
      .fn((_task: string, _signal: AbortSignal) => first.promise)
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);
    const { result } = renderHook(() => {
      const form = useRecommendationForm({ task: 'Больше тени' });
      return {
        form,
        task: useRecommendationTask({
          form,
          active: true,
          onInterpret,
          supportsBuildingScreen: true,
        }),
      };
    });
    let firstWork!: Promise<void>;
    let secondWork!: Promise<void>;
    act(() => {
      firstWork = result.current.task.interpret();
    });
    act(() => {
      secondWork = result.current.task.interpret();
    });
    expect(onInterpret.mock.calls[0][1].aborted).toBe(true);
    await act(async () => {
      second.resolve({ ...interpreted, profile: 'continuity', max_sites: 30 });
      await secondWork;
    });
    await act(async () => {
      first.resolve(interpreted);
      await firstWork;
    });
    expect(result.current.form.getValues()).toMatchObject({
      profile: 'continuity',
      maxSites: 30,
    });
    expect(result.current.task.stage).toBe('interpreted');
  });
  it('does not commit into another form owner after a project switch', async () => {
    const request = deferred();
    const { result, rerender } = renderHook(
      ({ project }) => {
        const first = useRecommendationForm({ task: 'Больше тени' });
        const second = useRecommendationForm({
          task: 'Другой проект',
          profile: 'continuity',
          maxSites: 70,
        });
        const form = project === 'first' ? first : second;
        return {
          first,
          second,
          task: useRecommendationTask({
            form,
            active: true,
            onInterpret: () => request.promise,
            supportsBuildingScreen: true,
          }),
        };
      },
      { initialProps: { project: 'first' } },
    );
    let work!: Promise<void>;
    act(() => {
      work = result.current.task.interpret();
    });
    rerender({ project: 'second' });
    await act(async () => {
      request.resolve(interpreted);
      await work;
    });
    expect(result.current.first.getValues()).toMatchObject({
      profile: 'balanced',
      maxSites: 40,
    });
    expect(result.current.second.getValues()).toMatchObject({
      profile: 'continuity',
      maxSites: 70,
    });
  });
});
