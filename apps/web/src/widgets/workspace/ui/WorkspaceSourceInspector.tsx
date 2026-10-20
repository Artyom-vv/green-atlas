import type { WorkspaceSourceInspectorProps } from './WorkspaceSourceInspector.props';
import type { FC } from 'react';
import { EditorActions, EditorPanel } from '@/shared/ui/inspector/EditorPanel';
import { Button } from '@green/ui';
export const WorkspaceSourceInspector: FC<WorkspaceSourceInspectorProps> = ({
  inspectorView,
  planLocked,
  sourceImportStatus,
  openLeftPanel,
  navigate,
  projectId,
}) => {
  if (inspectorView !== 'source') return null;
  const requiresBundle =
    planLocked && sourceImportStatus?.mode !== 'cad_preview';
  return (
    <EditorPanel title={planLocked ? 'Только просмотр' : 'Исходный чертёж'}>
      {planLocked && sourceImportStatus?.message && (
        <p className="m-0 text-xs leading-4 text-neutral-600">
          {sourceImportStatus.message}
        </p>
      )}
      <EditorActions grid>
        <Button variant="secondary" onClick={openLeftPanel}>
          Открыть слои
        </Button>
        <Button
          variant="primary"
          onClick={() =>
            navigate(
              `/projects/${projectId}/${requiresBundle ? 'import' : 'setup'}`,
            )
          }
        >
          {requiresBundle ? 'Загрузить полный ZIP' : 'Исходные данные'}
        </Button>
      </EditorActions>
    </EditorPanel>
  );
};
