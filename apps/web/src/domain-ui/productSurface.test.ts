import { describe, expect, it } from 'vitest';
import { api } from '@green/api-client';

describe('assisted planning product surface', () => {
  it('exposes only the DXF-to-change-set workflow to the web app', () => {
    expect(Object.keys(api).sort()).toEqual([
      'addPlanObject',
      'applyPlanChanges',
      'cancelOperation',
      'checkPlacement',
      'createExport',
      'createManualPlan',
      'createProject',
      'deletePlanObjects',
      'deleteProject',
      'downloadUrl',
      'getLatestOperation',
      'getMapFeatures',
      'getOperation',
      'getPlanHistory',
      'getProject',
      'listProjects',
      'previewPlanChanges',
      'redoPlanChange',
      'saveMappings',
      'savePlantingZones',
      'sourceDownloadUrl',
      'startGeometryOperation',
      'undoPlanChange',
      'updatePlanObject',
      'uploadDxf',
    ]);
  });
});
