import {
  CanvasControlGroup,
  CanvasControlSlot,
} from '@/shared/ui/CanvasControls';
import { Divider, IconButton } from '@green/ui';
import { Crosshair, LandPlot, Maximize2, Minus, Plus } from 'lucide-react';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface CanvasNavigationProps extends Pick<
  WorkspaceReadyModel,
  | 'focusZones'
  | 'mapViewport'
  | 'project'
  | 'sceneOpen'
  | 'selectedPatternZones'
> {}
export const CanvasNavigationPropsFor = (props: CanvasNavigationProps) => ({
  mapViewport: props.mapViewport,
  project: props.project,
  sceneOpen: props.sceneOpen,
  focusZones: props.focusZones,
  selectedPatternZones: props.selectedPatternZones,
});
export const CanvasNavigation: FC<CanvasNavigationProps> = ({
  mapViewport,
  project,
  sceneOpen,
  focusZones,
  selectedPatternZones,
}) => (
  <>
    {!sceneOpen && (
      <CanvasControlSlot position="bottomRight">
        <CanvasControlGroup orientation="vertical" label="Масштаб карты">
          <IconButton
            icon={Plus}
            label="Увеличить"
            variant="ghost"
            onClick={() => mapViewport.current?.zoomIn()}
          />
          <IconButton
            icon={Minus}
            label="Уменьшить"
            variant="ghost"
            onClick={() => mapViewport.current?.zoomOut()}
          />
          <Divider />
          <IconButton
            icon={Maximize2}
            label="Показать весь чертёж"
            variant="ghost"
            onClick={() => mapViewport.current?.fit()}
          />
          {selectedPatternZones.length > 0 && (
            <IconButton
              icon={LandPlot}
              label={`Показать выбранные участки (${selectedPatternZones.length})`}
              variant="ghost"
              onClick={() => focusZones(selectedPatternZones)}
            />
          )}
          {project.plan && (
            <IconButton
              icon={Crosshair}
              label="Показать посадки"
              variant="ghost"
              onClick={() => mapViewport.current?.fitPlan()}
            />
          )}
        </CanvasControlGroup>
      </CanvasControlSlot>
    )}
  </>
);
