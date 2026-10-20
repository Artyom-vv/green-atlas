import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface CanvasScopeProps extends Pick<
  WorkspaceReadyModel,
  | 'activeToolHint'
  | 'editorBusy'
  | 'ideRightTab'
  | 'rightOpen'
  | 'sceneOpen'
  | 'tool'
  | 'toolNames'
> {}
export const CanvasScopePropsFor = (props: CanvasScopeProps) => ({
  activeToolHint: props.activeToolHint,
  editorBusy: props.editorBusy,
  ideRightTab: props.ideRightTab,
  rightOpen: props.rightOpen,
  sceneOpen: props.sceneOpen,
  tool: props.tool,
  toolNames: props.toolNames,
});
export const CanvasScope: FC<CanvasScopeProps> = ({
  activeToolHint,
  editorBusy,
  ideRightTab,
  rightOpen,
  sceneOpen,
  tool,
  toolNames,
}) => (
  <>
    {!editorBusy &&
      !sceneOpen &&
      activeToolHint &&
      (!rightOpen || ideRightTab !== 'tool') && (
        <div
          className="rounded-control right-canvas-inset pointer-events-none absolute top-[calc(var(--spacing-canvas-inset)+var(--spacing-canvas-rail)+var(--spacing-canvas-gap))] left-[calc(var(--spacing-canvas-inset)+var(--spacing-canvas-rail)+var(--spacing-canvas-gap))] z-10 grid w-fit gap-1 border border-solid border-neutral-300 bg-white p-3 text-xs [&_span]:text-neutral-600 [&_strong]:font-semibold"
          role="status"
        >
          <strong>{toolNames[tool]}</strong>
          <span>{activeToolHint}</span>
        </div>
      )}
  </>
);
