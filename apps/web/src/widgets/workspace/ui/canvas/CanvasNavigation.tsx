import { MapControlGroup } from '@/widgets/map/ui/MapControlGroup';
import { IconButton } from '@green/ui';
import { Crosshair, Maximize2, Minus, Plus } from 'lucide-react';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface CanvasNavigationProps extends Pick<
  WorkspaceReadyModel,
  'mapViewport' | 'project' | 'sceneOpen'
> {}
export const CanvasNavigationPropsFor = (props: CanvasNavigationProps) => ({
  mapViewport: props.mapViewport,
  project: props.project,
  sceneOpen: props.sceneOpen,
});
export const CanvasNavigation: FC<CanvasNavigationProps> = ({
  mapViewport,
  project,
  sceneOpen,
}) => (
  <>
    {!sceneOpen && (
      <div className="absolute right-4 bottom-11 z-40 flex flex-col gap-2">
        <MapControlGroup orientation="vertical" label="Масштаб карты">
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
          <IconButton
            icon={Maximize2}
            label="Показать весь чертёж"
            variant="ghost"
            onClick={() => mapViewport.current?.fit()}
          />
        </MapControlGroup>
        {project.plan && (
          <MapControlGroup orientation="vertical" label="План озеленения">
            <IconButton
              icon={Crosshair}
              label="Показать посадки"
              variant="ghost"
              onClick={() => mapViewport.current?.fitPlan()}
            />
          </MapControlGroup>
        )}
      </div>
    )}
  </>
);
