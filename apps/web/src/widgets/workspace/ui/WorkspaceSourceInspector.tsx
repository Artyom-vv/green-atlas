import type { WorkspaceSourceInspectorProps } from './WorkspaceSourceInspector.props';
import type { FC } from 'react';
import { EditorActions, EditorPanel } from '@/shared/ui/inspector/EditorPanel';
import { Button, Disclosure } from '@green/ui';
export const WorkspaceSourceInspector: FC<WorkspaceSourceInspectorProps> = ({
  inspectorView,
  planLocked,
  sourceWarnings,
  sourceImportStatus,
  openLeftPanel,
  navigate,
  projectId,
}) => {
  if (inspectorView !== 'source') return null;
  const requiresBundle =
    planLocked && sourceImportStatus?.mode !== 'cad_preview';
  const warnings = sourceWarnings.filter(
    (warning) => warning !== sourceImportStatus?.message,
  );
  return (
    <EditorPanel title={planLocked ? 'Только просмотр' : 'Исходный DXF'}>
      <p className="m-0 text-xs leading-4 text-neutral-600">
        {planLocked
          ? (sourceImportStatus?.message ??
            'Для редактирования посадок загрузите полный ZIP-пакет выпуска.')
          : 'Назначьте роли слоям, затем выберите участок на карте.'}
      </p>
      {warnings.length > 0 && (
        <Disclosure
          variant="plain"
          title={`Замечания к файлу (${warnings.length})`}
        >
          <ul className="m-0 list-disc space-y-2 pl-4 text-xs leading-5 text-neutral-600">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Disclosure>
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
