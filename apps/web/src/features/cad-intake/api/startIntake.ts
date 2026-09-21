import {
  api,
  type CadDrawingEntry,
  type CadIntakeRequest,
  type ProjectOperation,
} from '@green/api-client';
import { startOrRecoverOperation } from './startOrRecoverOperation';

export interface IntakeSelection {
  rootId: string;
  path: string;
  sha256?: string;
  additionalEntries?: CadDrawingEntry[];
}

const sameEntries = (
  left: CadDrawingEntry[] | undefined,
  right: CadDrawingEntry[] | undefined,
) => JSON.stringify(left ?? []) === JSON.stringify(right ?? []);

function sameRequest(
  operation: ProjectOperation,
  request: CadIntakeRequest,
  version: number,
) {
  const saved = operation.cad_intake?.request;
  return (
    operation.project_state_version === version &&
    saved?.root_id === request.root_id &&
    saved.entry === request.entry &&
    saved.entry_sha256 === request.entry_sha256 &&
    sameEntries(saved.additional_entries, request.additional_entries) &&
    !saved.overrides?.length
  );
}

export async function startIntake(
  selection: IntakeSelection,
  projectId: string | undefined,
  onProject: (id: string) => void,
) {
  const fingerprint = selection.sha256
    ? { sha256: selection.sha256 }
    : await api.fingerprintCadDrawing(selection.rootId, selection.path);
  const name =
    selection.path
      .split('/')
      .at(-1)
      ?.replace(/\.(dwg|dxf)$/i, '') ?? 'Комплект CAD';
  const project = projectId
    ? await api.getProject(projectId, false)
    : await api.createProject(name);
  if (!project.id || !Number.isInteger(project.state_version))
    throw new Error('Сервер не подтвердил проект и его версию.');
  onProject(project.id);
  const request: CadIntakeRequest = {
    root_id: selection.rootId,
    entry: selection.path,
    entry_sha256: fingerprint.sha256,
    ...(selection.additionalEntries?.length
      ? { additional_entries: selection.additionalEntries }
      : {}),
  };
  const operation = await startOrRecoverOperation(
    project.id,
    'inspect_cad_package',
    (candidate) => sameRequest(candidate, request, project.state_version!),
    () =>
      api.startCadIntake(project.id!, request, {
        expectedStateVersion: project.state_version!,
      }),
  );
  return { projectId: project.id, operation };
}
