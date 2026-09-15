import { describe, expect, it } from 'vitest';
import {
  emptyReleaseForm,
  parseReleaseDraftDocument,
  releaseDraftRequest,
  sameReleaseContext,
  type ReleaseDraftDocument,
} from './releaseDraft';

function document(): ReleaseDraftDocument {
  return {
    version: 1,
    projectId: 'project-1',
    view: 'form',
    draft: {
      ...emptyReleaseForm(20),
      id: 'draft-1',
      revision: 4,
      context: { planId: 'plan-1', planVersion: 7, geometryVersion: 3 },
      editingReleaseId: 'release-previous',
    },
  };
}
function parse(value: unknown, projectId = 'project-1') {
  return parseReleaseDraftDocument(JSON.stringify(value), projectId);
}

describe('release draft document boundary', () => {
  it('starts separate forms without sharing mutable basis values', () => {
    const first = emptyReleaseForm(10),
      second = emptyReleaseForm(0);
    first.basis.pp616_reference = 'Решение первого проекта';
    expect(second.basis.pp616_reference).toBe('');
    expect(second).toMatchObject({ mode: 'draft', sceneHorizon: 0 });
    expect(first.sceneHorizon).toBe(10);
  });

  it('detects plan identity and geometry changes even when another version number stays the same', () => {
    const context = { planId: 'plan-1', planVersion: 7, geometryVersion: 3 };
    expect(sameReleaseContext(context, { ...context })).toBe(true);
    expect(
      sameReleaseContext(context, { ...context, planId: 'restored-plan' }),
    ).toBe(false);
    expect(sameReleaseContext(context, { ...context, planVersion: 8 })).toBe(
      false,
    );
    expect(
      sameReleaseContext(context, { ...context, geometryVersion: 4 }),
    ).toBe(false);
    expect(
      sameReleaseContext(context, { planVersion: 7, geometryVersion: 3 }),
    ).toBe(false);
  });

  it('keeps literal final basis in a detached request and omits hidden basis from a draft request', () => {
    const values = emptyReleaseForm(20);
    values.mode = 'final';
    values.basis = {
      pp616_status: 'documented',
      pp616_reference: '  Решение № 12\nДополнение пользователя: оставить  ',
      pp1160_status: 'not_required',
      pp1160_reference: '  Основание «Сад»  ',
      confirmed_by: '  И. И. Иванов  ',
    };
    const request = releaseDraftRequest(values);
    expect(request).toEqual({
      mode: 'final',
      scene_horizon: 20,
      regulatory_basis: values.basis,
    });
    expect(request.regulatory_basis).not.toBe(values.basis);
    values.basis.confirmed_by = 'Изменено после отправки';
    expect(request.regulatory_basis?.confirmed_by).toBe('  И. И. Иванов  ');
    expect(releaseDraftRequest({ ...values, mode: 'draft' })).toEqual({
      mode: 'draft',
      scene_horizon: 20,
    });
  });

  it('round trips maximum length references and preserves whitespace and marker-shaped text', () => {
    const value = document();
    value.draft!.mode = 'final';
    value.draft!.basis = {
      pp616_status: 'documented',
      pp616_reference: '  Дополнение пользователя:\n'.padEnd(240, 'я'),
      pp1160_status: 'documented',
      pp1160_reference: 'Ф'.repeat(240),
      confirmed_by: 'И'.repeat(160),
    };
    expect(value.draft!.basis.pp616_reference.length).toBe(240);
    const restored = parse(value);
    expect(restored).toEqual(value);
    expect(restored?.draft?.editingReleaseId).toBe('release-previous');
  });

  it.each([
    ['pp616_reference', 241],
    ['pp1160_reference', 241],
    ['confirmed_by', 161],
  ] as const)(
    'rejects an over-budget %s instead of silently truncating it',
    (field, length) => {
      const value = document();
      value.draft!.basis[field] = 'Ж'.repeat(length);
      expect(parse(value)).toBeUndefined();
      expect(value.draft!.basis[field]).toHaveLength(length);
    },
  );

  it('never restores another project document under the current project key', () => {
    const value = document();
    expect(parse(value, 'project-2')).toBeUndefined();
    expect(parse({ ...value, projectId: ['project-1'] })).toBeUndefined();
    expect(parse(value)?.projectId).toBe('project-1');
  });

  it.each([
    { field: 'view', value: ['form'] },
    { field: 'mode', value: ['final'] },
    { field: 'pp616_status', value: ['documented'] },
    { field: 'pp1160_status', value: ['not_required'] },
  ])(
    'rejects a JSON array disguised as the $field enum',
    ({ field, value }) => {
      const source = document();
      const altered =
        field === 'view'
          ? { ...source, view: value }
          : field === 'mode'
            ? { ...source, draft: { ...source.draft, mode: value } }
            : {
                ...source,
                draft: {
                  ...source.draft,
                  basis: { ...source.draft!.basis, [field]: value },
                },
              };
      expect(parse(altered)).toBeUndefined();
    },
  );

  it('rejects invalid version, numeric context, and malformed storage envelopes', () => {
    const value = document();
    expect(parse({ ...value, version: 2 })).toBeUndefined();
    for (const context of [
      { planVersion: 0, geometryVersion: 3 },
      { planVersion: 7, geometryVersion: -1 },
      { planVersion: 7.5, geometryVersion: 3 },
    ]) {
      expect(
        parse({ ...value, draft: { ...value.draft, context } }),
      ).toBeUndefined();
    }
    for (const sceneHorizon of [-1, 41, 1.5, '20'])
      expect(
        parse({ ...value, draft: { ...value.draft, sceneHorizon } }),
      ).toBeUndefined();
    expect(parseReleaseDraftDocument(null, 'project-1')).toBeUndefined();
    expect(parseReleaseDraftDocument('{broken', 'project-1')).toBeUndefined();
    expect(
      parseReleaseDraftDocument(' '.repeat(12_001), 'project-1'),
    ).toBeUndefined();
    expect(parse({ ...value, draft: [] })).toBeUndefined();
  });

  it.each(['pending', 'unknown'] as const)(
    'restores %s submission as unknown and rebuilds request only from validated fields',
    (phase) => {
      const value = document();
      value.draft!.mode = 'final';
      const submission = {
        id: 'submission-1',
        draftId: 'draft-1',
        draftRevision: 4,
        context: value.draft!.context,
        phase,
        request: {
          mode: 'forged',
          scene_horizon: 999,
          project_id: 'other-project',
        },
      };
      const restored = parse({ ...value, submission });
      expect(restored?.submission).toEqual({
        ...submission,
        phase: 'unknown',
        request: releaseDraftRequest(value.draft!),
      });
      expect(restored?.draft).toEqual(value.draft);
    },
  );

  it('rejects an interrupted submission that cannot be linked to its draft', () => {
    const value = document();
    const submission = {
      id: 'submission-1',
      draftId: 'other-draft',
      draftRevision: 4,
      context: value.draft!.context,
      phase: 'pending',
      request: releaseDraftRequest(value.draft!),
    };
    expect(parse({ ...value, submission })).toBeUndefined();
    expect(parse({ ...value, draft: undefined, submission })).toBeUndefined();
    expect(
      parse({
        ...value,
        submission: { ...submission, draftId: 'draft-1', phase: ['pending'] },
      }),
    ).toBeUndefined();
  });
});
