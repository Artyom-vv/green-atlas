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
      'createRelease',
      'deletePlanObjects',
      'deleteProject',
      'downloadUrl',
      'getDataPassport',
      'getLatestOperation',
      'getMapFeatures',
      'getOperation',
      'getPlanHistory',
      'getPlanScene',
      'getProject',
      'getRelease',
      'listPlacementMasks',
      'listProjects',
      'listSpecies',
      'previewBrush',
      'previewPlanChanges',
      'previewPlanPattern',
      'previewRecommendation',
      'redoPlanChange',
      'saveMappings',
      'savePlantingZones',
      'shortlistSpecies',
      'sourceDownloadUrl',
      'startGeometryOperation',
      'undoPlanChange',
      'updatePlanObject',
      'uploadDxf',
      'uploadReleaseBundle',
    ]);
  });
});
