import { EditorHeader } from '@/widgets/workbench/ui/EditorHeader';
import { Button, IconButton } from '@green/ui';
import { Package, Redo2, Undo2 } from 'lucide-react';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';

export interface WorkspaceHeaderProps extends Pick<
  WorkspaceReadyModel,
  | 'changePreview'
  | 'createRelease'
  | 'editorBusy'
  | 'historyQuery'
  | 'leaveWorkspace'
  | 'project'
  | 'projectHasPlan'
  | 'redoChange'
  | 'savingPlan'
  | 'setReleaseOpen'
  | 'undoChange'
> {}
export const WorkspaceHeaderPropsFor = (model: WorkspaceReadyModel) => ({
  changePreview: model.changePreview,
  createRelease: model.createRelease,
  editorBusy: model.editorBusy,
  historyQuery: model.historyQuery,
  leaveWorkspace: model.leaveWorkspace,
  project: model.project,
  projectHasPlan: model.projectHasPlan,
  redoChange: model.redoChange,
  savingPlan: model.savingPlan || model.assistant.pending === 'applying',
  setReleaseOpen: model.setReleaseOpen,
  undoChange: model.undoChange,
});
export const WorkspaceHeader: FC<WorkspaceHeaderProps> = ({
  changePreview,
  createRelease,
  editorBusy,
  historyQuery,
  leaveWorkspace,
  project,
  projectHasPlan,
  redoChange,
  savingPlan,
  setReleaseOpen,
  undoChange,
}) => (
  <EditorHeader name={project.name} onBack={() => leaveWorkspace('/projects')}>
    <IconButton
      icon={Undo2}
      label="Отменить"
      variant="ghost"
      disabled={
        !historyQuery.data?.can_undo || editorBusy || Boolean(changePreview)
      }
      onClick={() => undoChange.mutate()}
    />
    <IconButton
      icon={Redo2}
      label="Повторить"
      variant="ghost"
      disabled={
        !historyQuery.data?.can_redo || editorBusy || Boolean(changePreview)
      }
      onClick={() => redoChange.mutate()}
    />
    <span aria-hidden="true" className="mx-1 h-5 w-px bg-neutral-200" />
    {savingPlan && <span role="status">Сохраняем…</span>}
    <Button
      icon={Package}
      variant="primary"
      disabled={!projectHasPlan || (editorBusy && !createRelease.isPending)}
      onClick={() => setReleaseOpen(true)}
    >
      Выпуск
    </Button>
  </EditorHeader>
);
