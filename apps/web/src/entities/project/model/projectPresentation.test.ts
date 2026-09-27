import { describe, expect, it } from 'vitest';
import type { ProjectSummary } from '@green/api-client';
import { projectRoute, projectState } from './projectPresentation';

const imported = {
  id: 'native-capture',
  source_name: 'Генплан.dwg',
  has_geometry: true,
  status: 'imported',
  planting_zone_count: 0,
  plan_object_count: 0,
} as ProjectSummary;

describe('project list content, not calculation promises', () => {
  it('does not call a fresh live capture a calculated map', () => {
    expect(projectState(imported)).toEqual({
      title: 'Выберите участки', detail: 'Геометрия открыта',
    });
    expect(projectRoute(imported)).toBe('/projects/native-capture/workspace');
  });

  it('does not infer planting readiness from the presence of a zone', () => {
    expect(projectState({ ...imported, planting_zone_count: 1 }).title)
      .toBe('Участки созданы');
  });

  it('does not require the obsolete direct DXF upload for an empty project', () => {
    expect(projectState({ ...imported, source_name: null })).toEqual({
      title: 'Нужен исходник', detail: 'Откройте чертёж через AutoCAD',
    });
  });
});
