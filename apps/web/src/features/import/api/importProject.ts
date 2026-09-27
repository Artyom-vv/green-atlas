import { ApiClientError, type api, type Project } from '@green/api-client';
import { hasFixedProjectSource } from '../model/importProject';
import {
  isReleaseBundle,
  projectNameFromFile,
  validateProjectUpload,
} from '../model/uploadPolicy';

export type ProjectImportApi = Pick<
  typeof api,
  'getProject' | 'createProject' | 'uploadDxf' | 'uploadReleaseBundle'
>;

export type ImportedProject = Project & { id: string };

function requireProjectId(
  project: Project,
): asserts project is ImportedProject {
  if (!project.id) {
    throw new ApiClientError(
      'PROJECT_ID_MISSING',
      'Сервер не вернул идентификатор проекта.',
    );
  }
}

export interface ImportProjectRequest {
  file: File;
  projectId?: string;
  onProjectResolved: (projectId: string) => void;
}

export async function importProject(
  client: ProjectImportApi,
  request: ImportProjectRequest,
): Promise<ImportedProject> {
  const validationError = validateProjectUpload(request.file);
  if (validationError) throw new Error(validationError);

  // Creating a project is a write. Retain its identity even if the caller
  // leaves the route before creation/upload completes.
  const project = request.projectId
    ? await client.getProject(request.projectId, false)
    : await client.createProject(projectNameFromFile(request.file.name));
  requireProjectId(project);
  request.onProjectResolved(project.id);
  if (hasFixedProjectSource(project)) {
    throw new Error(
      'Исходный чертёж зафиксирован. Для другого файла создайте новый проект.',
    );
  }
  const imported = await (isReleaseBundle(request.file.name)
    ? client.uploadReleaseBundle(project.id, request.file)
    : client.uploadDxf(project.id, request.file));
  requireProjectId(imported);
  return imported;
}
