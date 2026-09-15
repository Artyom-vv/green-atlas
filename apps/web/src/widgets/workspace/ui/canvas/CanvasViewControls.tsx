import { MapViewSwitch } from '@/widgets/map/ui/MapViewSwitch';
import { Select } from '@green/ui';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface CanvasViewControlsProps extends Pick<
  WorkspaceReadyModel,
  | 'changeMapMode'
  | 'hasUnsavedWork'
  | 'mapRenderMode'
  | 'project'
  | 'sceneOpen'
  | 'setMapRenderMode'
  | 'setPendingScene'
> {}
export const CanvasViewControlsPropsFor = (props: CanvasViewControlsProps) => ({
  changeMapMode: props.changeMapMode,
  hasUnsavedWork: props.hasUnsavedWork,
  mapRenderMode: props.mapRenderMode,
  project: props.project,
  sceneOpen: props.sceneOpen,
  setMapRenderMode: props.setMapRenderMode,
  setPendingScene: props.setPendingScene,
});
export const CanvasViewControls: FC<CanvasViewControlsProps> = ({
  changeMapMode,
  hasUnsavedWork,
  mapRenderMode,
  project,
  sceneOpen,
  setMapRenderMode,
  setPendingScene,
}) => (
  <>
    <div
      className="absolute top-3 right-3 z-40 flex max-w-[calc(100%-5rem)] flex-wrap items-center justify-end gap-2"
      role="group"
      aria-label="Отображение карты"
    >
      {!sceneOpen && (
        <Select
          controlSize="compact"
          className="min-w-42 shrink-0"
          aria-label="Подложка карты"
          value={mapRenderMode}
          onChange={(event) =>
            setMapRenderMode(event.target.value as 'design' | 'cad')
          }
        >
          <option value="design">Проектный вид</option>
          <option value="cad">Исходный DXF</option>
        </Select>
      )}
      {project.map_ready && (
        <MapViewSwitch
          mode={sceneOpen ? '3d' : '2d'}
          onChange={(mode) => {
            if (mode === '3d' && hasUnsavedWork) {
              setPendingScene(true);
              return;
            }
            changeMapMode(mode);
          }}
        />
      )}
    </div>
  </>
);
