import { describe, expect, it } from 'vitest';
import { api } from '@green/api-client';

describe('manual planning product surface', () => {
  it('exposes only the DXF-to-manual-plan workflow to the web app', () => {
    expect(Object.keys(api).sort()).toEqual([
      'addPlanObject',
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
