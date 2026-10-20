import { describe, expect, it } from 'vitest';
import { workspaceFeedback } from './workspaceFeedback';

const idle = {
  placementPending: false,
  localToolVisible: false,
  localWriteVisible: false,
  savingPlan: false,
  releasePending: false,
  releaseVisible: false,
};
describe('operation feedback ownership', () => {
  it('keeps a scenario check in its visible panel and resumes the header when the panel closes', () => {
    const check = {
      ...idle,
      placementPending: true,
      placementPhase: 'checking' as const,
      localToolVisible: true,
    };
    expect(workspaceFeedback(check)).toMatchObject({
      quietPersistence: true,
      headerWrite: false,
      headerPlacement: false,
      placementLabel: 'Проверяем размещение',
    });
    expect(
      workspaceFeedback({
        ...check,
        savingPlan: false,
        placementPhase: 'checking',
      }),
    ).toMatchObject({
      quietPersistence: true,
      headerWrite: false,
      headerPlacement: false,
    });
    expect(
      workspaceFeedback({
        ...check,
        savingPlan: false,
        localToolVisible: false,
      }),
    ).toMatchObject({ quietPersistence: false, headerPlacement: true });
  });
  it('does not suppress an independent write when the release dialog owns a release', () => {
    expect(
      workspaceFeedback({
        ...idle,
        savingPlan: true,
        releasePending: true,
        releaseVisible: true,
      }),
    ).toMatchObject({ headerRelease: false, headerWrite: true });
    expect(workspaceFeedback({ ...idle, releasePending: true })).toMatchObject({
      headerRelease: true,
    });
  });
  it('does not declare an idle task busy and transfers visible modal writes to their local action', () => {
    expect(workspaceFeedback(idle)).toMatchObject({
      quietPersistence: false,
      headerPlacement: false,
      headerWrite: false,
      headerRelease: false,
    });
    expect(
      workspaceFeedback({ ...idle, savingPlan: true, localWriteVisible: true }),
    ).toMatchObject({ quietPersistence: true, headerWrite: false });
  });
});
