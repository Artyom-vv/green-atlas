import { describe, expect, it } from 'vitest';
import type { ProjectSummary } from '@green/api-client';
import { projectResumeRoute } from './projectResume';

const project = {
  id: 'saved',
  source_name: 'site.dwg',
  has_geometry: true,
  status: 'editing',
} as ProjectSummary;
describe('saved project resume policy', () => {
  it('uses the server entry point even when old geometry metadata suggests workspace', () => {
    expect(
      projectResumeRoute({ ...project, resume_kind: 'source_preparation' }),
    ).toBe('/projects/saved/setup');
  });
  it('opens saved work without another CAD transfer', () => {
    expect(projectResumeRoute({ ...project, resume_kind: 'workspace' })).toBe(
      '/projects/saved/workspace',
    );
  });
  it('directs an empty legacy record to instructions without an upload or a new empty record', () => {
    expect(projectResumeRoute({ ...project, source_name: null })).toBe(
      '/projects/new',
    );
    expect(
      projectResumeRoute({ ...project, resume_kind: 'awaiting_autocad' }),
    ).toBe('/projects/new');
  });
});
