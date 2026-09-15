import { describe, expect, it } from 'vitest';
import { ProjectVersions, requestScope } from './concurrency';

const projectId = 'versioned-project';
const projectPath = `/api/projects/${projectId}`;
const stateRead = requestScope(projectPath);
const write = requestScope(`${projectPath}/plan/objects`, 'POST');

function headers(version?: string): Headers {
  return new Headers(version ? { 'X-Project-State-Version': version } : {});
}

function seededVersions(): ProjectVersions {
  const versions = new ProjectVersions();
  versions.observe(stateRead, headers('7'), {
    id: projectId,
    state_version: 7,
  });
  return versions;
}

describe('project snapshot ownership', () => {
  it.each([
    '/plan/placement-check',
    '/plan/change-sets/preview',
    '/plan/patterns/preview',
    '/plan/recommendations/preview',
    '/plan/brush/preview',
    '/building-screen/preview',
    '/species/shortlist',
  ])('keeps %s read-only after a committed edit', (suffix) => {
    const versions = seededVersions();
    versions.observe(write, headers('8'), { state_version: 8, plan: {} });
    const preview = requestScope(projectPath + suffix, 'POST');
    expect(versions.headers(preview).get('If-Match')).toBeNull();
    versions.observe(preview, headers('9'), {
      id: projectId,
      state_version: 9,
    });
    expect(versions.headers(write).get('If-Match')).toBe('"8"');
  });

  it.each(['/plan/history', '/map-features', '/plan/scene', '/operations/job'])(
    'does not adopt the version of a delayed read projection %s',
    (suffix) => {
      const versions = seededVersions();
      versions.observe(write, headers('8'), {});
      versions.observe(requestScope(projectPath + suffix), headers('6'), {});
      expect(versions.headers(write).get('If-Match')).toBe('"8"');
    },
  );

  it.each([undefined, '', '0', '-1', 'NaN', '1.5'])(
    'preserves the known version when a projection header is %s',
    (version) => {
      const versions = seededVersions();
      versions.observe(write, headers(version), { added_ids: ['tree'] });
      expect(versions.headers(write).get('If-Match')).toBe('"7"');
    },
  );

  it('learns a conversation confirmation without attaching If-Match', () => {
    const versions = seededVersions();
    const confirm = requestScope(
      `${projectPath}/conversations/chat/proposals/proposal/confirm`,
      'POST',
    );
    expect(versions.headers(confirm).get('If-Match')).toBeNull();
    versions.observe(confirm, headers('8'), { state_version: 8, plan: {} });
    expect(versions.headers(write).get('If-Match')).toBe('"8"');
  });

  it('preserves an explicit snapshot and the caller headers', () => {
    const versions = seededVersions();
    const source = new Headers({
      'If-Match': '"4"',
      'Content-Type': 'application/json',
    });
    const result = versions.headers(write, source);
    expect(result.get('If-Match')).toBe('"4"');
    result.set('Content-Type', 'text/plain');
    expect(source.get('Content-Type')).toBe('application/json');
  });

  it('learns versions independently from a project list and undo', () => {
    const versions = new ProjectVersions();
    versions.observe(requestScope('/api/projects'), headers(), [
      { id: projectId, state_version: 7 },
      { id: 'second', state_version: 2 },
    ]);
    versions.observe(
      requestScope(`${projectPath}/plan/history/undo`, 'POST'),
      headers('8'),
      {
        id: projectId,
        state_version: 8,
      },
    );
    expect(versions.headers(write).get('If-Match')).toBe('"8"');
    expect(
      versions
        .headers(requestScope('/api/projects/second/exports', 'POST'))
        .get('If-Match'),
    ).toBe('"2"');
  });
});
