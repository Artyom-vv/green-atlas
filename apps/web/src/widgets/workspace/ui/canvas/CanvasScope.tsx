import { Button } from '@green/ui';
import { Crosshair } from 'lucide-react';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface CanvasScopeProps extends Pick<
  WorkspaceReadyModel,
  | 'activeToolHint'
  | 'focusZones'
  | 'ideRightTab'
  | 'rightOpen'
  | 'sceneOpen'
  | 'selectedPatternZones'
  | 'tool'
  | 'toolNames'
> {}
export const CanvasScopePropsFor = (props: CanvasScopeProps) => ({
  activeToolHint: props.activeToolHint,
  focusZones: props.focusZones,
  ideRightTab: props.ideRightTab,
  rightOpen: props.rightOpen,
  sceneOpen: props.sceneOpen,
  selectedPatternZones: props.selectedPatternZones,
  tool: props.tool,
  toolNames: props.toolNames,
});
export const CanvasScope: FC<CanvasScopeProps> = ({
  activeToolHint,
  focusZones,
  ideRightTab,
  rightOpen,
  sceneOpen,
  selectedPatternZones,
  tool,
  toolNames,
}) => (
  <>
    {!sceneOpen && activeToolHint && (!rightOpen || ideRightTab !== 'tool') && (
      <div
        className="rounded-card pointer-events-none absolute top-16 left-18 z-10 grid max-w-[calc(100%-6rem)] gap-1 border border-solid border-neutral-200 bg-white px-3 py-2 text-xs [&_span]:text-neutral-600 [&_strong]:font-semibold"
        role="status"
      >
        <strong>{toolNames[tool]}</strong>
        <span>{activeToolHint}</span>
      </div>
    )}
    {selectedPatternZones.length > 0 && (
      <Button
        className="absolute bottom-11 left-4 z-40 bg-white"
        variant="secondary"
        controlSize="compact"
        icon={Crosshair}
        onClick={() => focusZones(selectedPatternZones)}
      >{`Участки: ${selectedPatternZones.length}`}</Button>
    )}
  </>
);
