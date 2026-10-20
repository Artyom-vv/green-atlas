import { describe, expect, it } from 'vitest';
import type {
  NativeFaceDecision,
  Project,
  SourceAreaGroupRequest,
  SourceObjectDecisionRequest,
} from '@green/api-client';
import {
  matchesAreaGroup,
  matchesNativeFaceDecision,
  matchesObjectDecision,
  matchesRemovedAreaGroup,
} from './sourceDecisionProof';

const sha = 'a'.repeat(64);
const decision: SourceObjectDecisionRequest = {
  source_sha256: sha,
  source: { handle: 'BB', instance_chain: ['AA'] },
  geometry_sha256: 'b'.repeat(64),
  interpretation: 'linear',
};
const sourceProject = (source: Record<string, unknown>) =>
  ({
    id: 'project',
    state_version: 4,
    source_file: { content_sha256: sha, ...source },
  }) as unknown as Project;

describe('current source decisions', () => {
  it('requires the selected instance and geometry, rather than another matching handle', () => {
    expect(
      matchesObjectDecision(
        sourceProject({ object_decisions: [decision] }),
        decision,
      ),
    ).toBe(true);
    expect(
      matchesObjectDecision(
        sourceProject({
          object_decisions: [
            {
              ...decision,
              source: { ...decision.source, instance_chain: ['Other'] },
            },
          ],
        }),
        decision,
      ),
    ).toBe(false);
    expect(
      matchesObjectDecision(
        sourceProject({
          object_decisions: [{ ...decision, geometry_sha256: 'c'.repeat(64) }],
        }),
        decision,
      ),
    ).toBe(false);
    expect(
      matchesObjectDecision(
        sourceProject({
          object_decisions: [decision],
          content_sha256: 'd'.repeat(64),
        }),
        decision,
      ),
    ).toBe(false);
  });
  it('requires an explicitly read collection before proving removal or the default area interpretation', () => {
    expect(
      matchesObjectDecision(sourceProject({ object_decisions: [] }), {
        ...decision,
        interpretation: 'area',
      }),
    ).toBe(true);
    expect(
      matchesObjectDecision(sourceProject({}), {
        ...decision,
        interpretation: 'area',
      }),
    ).toBe(false);
    expect(
      matchesRemovedAreaGroup(sourceProject({ area_groups: [] }), 'group'),
    ).toBe(true);
    expect(matchesRemovedAreaGroup(sourceProject({}), 'group')).toBe(false);
  });
  it('matches an area role override separately from removing the object decision', () => {
    const request: SourceObjectDecisionRequest = {
      ...decision,
      interpretation: 'area',
      kind: 'lawn',
    };
    expect(
      matchesObjectDecision(
        sourceProject({ object_decisions: [request] }),
        request,
      ),
    ).toBe(true);
    expect(
      matchesObjectDecision(sourceProject({ object_decisions: [] }), request),
    ).toBe(false);
    expect(
      matchesObjectDecision(
        sourceProject({ object_decisions: [{ ...request, kind: 'road' }] }),
        request,
      ),
    ).toBe(false);
    expect(
      matchesObjectDecision(sourceProject({ object_decisions: [request] }), {
        ...request,
        kind: null,
      }),
    ).toBe(false);
    expect(
      matchesObjectDecision(sourceProject({ object_decisions: [] }), {
        ...request,
        kind: null,
      }),
    ).toBe(true);
  });
  it('matches a group by all source members and purpose regardless of ordering', () => {
    const request: SourceAreaGroupRequest = {
      source_sha256: sha,
      kind: 'building',
      members: [
        decision,
        { ...decision, source: { ...decision.source, handle: 'CC' } },
      ],
    };
    expect(
      matchesAreaGroup(
        sourceProject({
          area_groups: [
            {
              ...request,
              id: 'group',
              members: [...request.members].reverse(),
            },
          ],
        }),
        request,
      ),
    ).toBe(true);
    expect(
      matchesAreaGroup(
        sourceProject({
          area_groups: [
            { ...request, id: 'group', members: request.members.slice(0, 1) },
          ],
        }),
        request,
      ),
    ).toBe(false);
    expect(
      matchesAreaGroup(
        sourceProject({
          area_groups: [{ ...request, id: 'group', kind: 'road' }],
        }),
        request,
      ),
    ).toBe(false);
  });
  it('proves the chosen face goal from source metadata without a native geometry query', () => {
    const request: NativeFaceDecision = {
      source_sha256: sha,
      key: 'c'.repeat(64),
      rejected: true,
    };
    expect(
      matchesNativeFaceDecision(
        sourceProject({ rejected_native_face_keys: [request.key] }),
        request,
      ),
    ).toBe(true);
    expect(
      matchesNativeFaceDecision(
        sourceProject({ rejected_native_face_keys: [] }),
        request,
      ),
    ).toBe(false);
    expect(
      matchesNativeFaceDecision(
        sourceProject({ rejected_native_face_keys: [] }),
        { ...request, rejected: false },
      ),
    ).toBe(true);
    expect(
      matchesNativeFaceDecision(sourceProject({}), {
        ...request,
        rejected: false,
      }),
    ).toBe(false);
  });
});
