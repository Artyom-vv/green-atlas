import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@green/api-client';
import {
  emptyReleaseForm,
  releaseDraftRequest,
  type ReleaseDraft,
} from './releaseDraft';
import {
  getReleaseDraftSnapshot,
  releaseDraftKey,
  resetReleaseDraftMemory,
  subscribeReleaseDraft,
  updateReleaseDraft,
} from './releaseDraftStorage';

function draft(id = 'draft-1'): ReleaseDraft {
  return {
    ...emptyReleaseForm(20),
    id,
    revision: 2,
    context: { planId: 'plan-1', planVersion: 7, geometryVersion: 3 },
  };
}
const releaseKey = (projectId: string) => `green-atlas:release:${projectId}`;
beforeEach(() => {
  resetReleaseDraftMemory();
  window.sessionStorage.clear();
  window.localStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
  resetReleaseDraftMemory();
  window.sessionStorage.clear();
  window.localStorage.clear();
});

describe('project release draft storage', () => {
  it('preserves the editing form across reload separately from the authoritative release pointer', () => {
    const values = draft();
    values.mode = 'final';
    values.basis.confirmed_by =
      '  И. Иванов\nДополнение пользователя: literal  ';
    updateReleaseDraft('project-1', (current) => ({
      ...current,
      view: 'form',
      draft: values,
      releaseId: 'release-1',
      error: new Error('Временная ошибка'),
    }));
    const stored = JSON.parse(
      window.sessionStorage.getItem(releaseDraftKey('project-1'))!,
    );
    expect(stored).toEqual({
      version: 1,
      projectId: 'project-1',
      view: 'form',
      draft: values,
    });
    expect(window.localStorage.getItem(releaseKey('project-1'))).toBe(
      'release-1',
    );
    expect(window.localStorage.length).toBe(1);
    resetReleaseDraftMemory();
    expect(getReleaseDraftSnapshot('project-1')).toMatchObject({
      draft: values,
      view: 'form',
      releaseId: 'release-1',
      error: null,
      storageAvailable: true,
    });
  });

  it('keeps stable snapshots until a real update and notifies only the owning project', () => {
    const first = getReleaseDraftSnapshot('project-1'),
      second = getReleaseDraftSnapshot('project-2');
    const firstListener = vi.fn(() => getReleaseDraftSnapshot('project-1'));
    const secondListener = vi.fn();
    const unsubscribe = subscribeReleaseDraft('project-1', firstListener);
    subscribeReleaseDraft('project-2', secondListener);
    expect(getReleaseDraftSnapshot('project-1')).toBe(first);
    expect(updateReleaseDraft('project-1', (current) => current)).toBe(first);
    expect(firstListener).not.toHaveBeenCalled();
    const changed = updateReleaseDraft('project-1', (current) => ({
      ...current,
      draft: draft(),
    }));
    expect(changed).not.toBe(first);
    expect(first.draft).toBeUndefined();
    expect(firstListener).toHaveBeenCalledTimes(1);
    expect(firstListener.mock.results[0].value).toBe(changed);
    expect(getReleaseDraftSnapshot('project-2')).toBe(second);
    expect(secondListener).not.toHaveBeenCalled();
    unsubscribe();
    updateReleaseDraft('project-1', (current) => ({
      ...current,
      view: 'files',
    }));
    expect(firstListener).toHaveBeenCalledTimes(1);
  });

  it('serializes updates against the latest revision and keeps each project payload isolated', () => {
    updateReleaseDraft('project-1', (current) => ({
      ...current,
      draft: draft(),
    }));
    updateReleaseDraft('project-2', (current) => ({
      ...current,
      draft: draft('draft-2'),
    }));
    updateReleaseDraft('project-1', (current) => ({
      ...current,
      draft: {
        ...current.draft!,
        revision: current.draft!.revision + 1,
        basis: {
          ...current.draft!.basis,
          pp616_reference: 'Решение первого проекта',
        },
      },
    }));
    updateReleaseDraft('project-1', (current) => ({
      ...current,
      draft: {
        ...current.draft!,
        revision: current.draft!.revision + 1,
        basis: {
          ...current.draft!.basis,
          pp1160_reference: 'Второе основание',
        },
      },
    }));
    expect(getReleaseDraftSnapshot('project-1').draft).toMatchObject({
      revision: 4,
      basis: {
        pp616_reference: 'Решение первого проекта',
        pp1160_reference: 'Второе основание',
      },
    });
    expect(getReleaseDraftSnapshot('project-2').draft).toEqual(
      draft('draft-2'),
    );
    resetReleaseDraftMemory();
    expect(getReleaseDraftSnapshot('project-1').draft?.revision).toBe(4);
    expect(getReleaseDraftSnapshot('project-2').draft).toEqual(
      draft('draft-2'),
    );
  });

  it('retains an in-flight owner on remount and marks it unknown on real reload without a POST', () => {
    const values = draft();
    const submission = {
      id: 'submission-1',
      draftId: values.id,
      draftRevision: values.revision,
      context: values.context,
      request: releaseDraftRequest(values),
      phase: 'pending' as const,
    };
    const create = vi.spyOn(api, 'createRelease');
    const pending = updateReleaseDraft('project-1', (current) => ({
      ...current,
      draft: values,
      submission,
    }));
    expect(getReleaseDraftSnapshot('project-1')).toBe(pending);
    expect(pending.submission?.phase).toBe('pending');
    resetReleaseDraftMemory();
    const restored = getReleaseDraftSnapshot('project-1');
    expect(restored.submission).toEqual({ ...submission, phase: 'unknown' });
    expect(restored.draft).toEqual(values);
    expect(create).not.toHaveBeenCalled();
  });

  it('applies a late completion only to its original project while another project is open', () => {
    updateReleaseDraft('project-1', (current) => ({
      ...current,
      draft: draft(),
    }));
    const second = updateReleaseDraft('project-2', (current) => ({
      ...current,
      draft: draft('draft-2'),
      releaseId: 'release-2',
    }));
    const secondListener = vi.fn();
    subscribeReleaseDraft('project-2', secondListener);
    updateReleaseDraft('project-1', (current) => ({
      ...current,
      view: 'files',
      draft: undefined,
      submission: undefined,
      releaseId: 'release-1',
    }));
    expect(getReleaseDraftSnapshot('project-2')).toBe(second);
    expect(secondListener).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(releaseKey('project-1'))).toBe(
      'release-1',
    );
    expect(window.localStorage.getItem(releaseKey('project-2'))).toBe(
      'release-2',
    );
    expect(
      window.sessionStorage.getItem(releaseDraftKey('project-1')),
    ).toBeNull();
    resetReleaseDraftMemory();
    expect(getReleaseDraftSnapshot('project-1')).toMatchObject({
      view: 'files',
      releaseId: 'release-1',
    });
    expect(getReleaseDraftSnapshot('project-2').draft?.id).toBe('draft-2');
  });

  it('ignores a corrupted cross-project draft without discarding the saved release pointer', () => {
    window.sessionStorage.setItem(
      releaseDraftKey('project-1'),
      JSON.stringify({
        version: 1,
        projectId: 'project-2',
        view: 'form',
        draft: draft('foreign'),
      }),
    );
    window.localStorage.setItem(releaseKey('project-1'), 'release-1');
    expect(getReleaseDraftSnapshot('project-1')).toMatchObject({
      projectId: 'project-1',
      view: 'files',
      releaseId: 'release-1',
    });
    expect(getReleaseDraftSnapshot('project-1').draft).toBeUndefined();
  });

  it('preserves in-memory form continuity when storage reads and writes are unavailable', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('Blocked', 'SecurityError');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Blocked', 'SecurityError');
    });
    expect(getReleaseDraftSnapshot('project-1').storageAvailable).toBe(false);
    const saved = updateReleaseDraft('project-1', (current) => ({
      ...current,
      draft: draft(),
    }));
    expect(saved.storageAvailable).toBe(false);
    expect(getReleaseDraftSnapshot('project-1')).toBe(saved);
    const edited = updateReleaseDraft('project-1', (current) => ({
      ...current,
      draft: { ...current.draft!, sceneHorizon: 10 },
    }));
    expect(getReleaseDraftSnapshot('project-1').draft?.sceneHorizon).toBe(10);
    expect(edited.storageAvailable).toBe(false);
    expect(getReleaseDraftSnapshot('project-2').draft).toBeUndefined();
  });

  it('recovers persistence on the next edit after quota failure without losing the earlier values', () => {
    const write = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(() => {
        throw new DOMException('Full', 'QuotaExceededError');
      });
    const values = draft();
    values.basis.pp616_reference = 'Не терять это основание';
    updateReleaseDraft('project-1', (current) => ({
      ...current,
      draft: values,
    }));
    expect(getReleaseDraftSnapshot('project-1').storageAvailable).toBe(false);
    write.mockRestore();
    updateReleaseDraft('project-1', (current) => ({
      ...current,
      draft: { ...current.draft!, revision: 3, sceneHorizon: 5 },
    }));
    expect(getReleaseDraftSnapshot('project-1').storageAvailable).toBe(true);
    resetReleaseDraftMemory();
    expect(getReleaseDraftSnapshot('project-1').draft).toMatchObject({
      revision: 3,
      sceneHorizon: 5,
      basis: { pp616_reference: 'Не терять это основание' },
    });
  });

  it('does not erase a newer release pointer when clearing an older in-memory release', () => {
    updateReleaseDraft('project-1', (current) => ({
      ...current,
      releaseId: 'release-1',
    }));
    window.localStorage.setItem(releaseKey('project-1'), 'release-newer');
    updateReleaseDraft('project-1', (current) => ({
      ...current,
      releaseId: undefined,
    }));
    expect(window.localStorage.getItem(releaseKey('project-1'))).toBe(
      'release-newer',
    );
  });
});
