import { api } from '@green/api-client';
import type { DeleteSelectionContext } from '../model/deleteSelection';

export const deleteSelection = (context: DeleteSelectionContext) =>
  api.deletePlanObjects(context.projectId, context.ids, {
    expectedStateVersion: context.expectedStateVersion,
  });

export const readDeletionProject = (projectId: string) =>
  api.getProject(projectId, false);
