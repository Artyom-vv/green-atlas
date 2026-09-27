import { api } from '@green/api-client';
import type { ZoneCommandContext } from '../model/zoneCommand';

export function saveZoneSnapshot(context: ZoneCommandContext) {
  return api.savePlantingZones(context.projectId, context.zones, {
    expectedStateVersion: context.expectedStateVersion,
  });
}

export function readZoneProject(projectId: string) {
  return api.getProject(projectId, false);
}
