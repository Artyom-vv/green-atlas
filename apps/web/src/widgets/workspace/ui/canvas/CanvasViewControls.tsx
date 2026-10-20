import { MapViewSwitch } from '@/widgets/map/ui/MapViewSwitch';
import { MapLegend } from '@/widgets/map/ui/MapLegend';
import { MapPlanComparison } from '@/widgets/map/ui/MapPlanComparison';
import {
  Button,
  Divider,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuRadioGroup,
  MenuRadioItem,
  MenuGroupLabel,
  MenuGroup,
} from '@green/ui';
import { Check, ChevronDown, Clock3 } from 'lucide-react';
import { IconButton } from '@green/ui';
import {
  CanvasControlGroup,
  CanvasControlSlot,
  canvasSurfaceClassName,
} from '@/shared/ui/CanvasControls';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface CanvasViewControlsProps extends Pick<
  WorkspaceReadyModel,
  | 'changeMapMode'
  | 'hasUnsavedWork'
  | 'mapRenderMode'
  | 'project'
  | 'planComparison'
  | 'sceneOpen'
  | 'setMapRenderMode'
  | 'setPendingScene'
> {
  embedded?: boolean;
  growthScenario?: WorkspaceReadyModel['growthScenario'];
}
export const CanvasViewControlsPropsFor = (props: CanvasViewControlsProps) => ({
  changeMapMode: props.changeMapMode,
  hasUnsavedWork: props.hasUnsavedWork,
  mapRenderMode: props.mapRenderMode,
  project: props.project,
  planComparison: props.planComparison,
  sceneOpen: props.sceneOpen,
  setMapRenderMode: props.setMapRenderMode,
  setPendingScene: props.setPendingScene,
  growthScenario: props.growthScenario,
});
export const CanvasViewControls: FC<CanvasViewControlsProps> = ({
  changeMapMode,
  hasUnsavedWork,
  mapRenderMode,
  project,
  planComparison,
  sceneOpen,
  setMapRenderMode,
  setPendingScene,
  growthScenario,
  embedded = false,
}) => {
  const controls = (
    <>
      {!sceneOpen && (
        <Menu>
          <MenuTrigger
            render={
              <Button
                variant="ghost"
                controlSize="compact"
                className="w-24 justify-between border-0 px-2 text-left data-[popup-open]:bg-neutral-100"
                endIcon={<ChevronDown />}
                aria-label={`Подложка карты: ${mapRenderMode === 'design' ? 'проектный вид' : 'геометрия CAD'}`}
              />
            }
          >
            {mapRenderMode === 'design' ? 'Проект' : 'CAD'}
          </MenuTrigger>
          <MenuPopup
            sideOffset={13}
            className={`${canvasSurfaceClassName} w-64 cursor-default shadow-none [scrollbar-gutter:auto]`}
          >
            <MenuGroup>
              <MenuGroupLabel className="px-3 py-2 text-xs text-neutral-600">
                Подложка карты
              </MenuGroupLabel>
              <MenuRadioGroup
                value={mapRenderMode}
                onValueChange={(value) =>
                  setMapRenderMode(value as 'design' | 'cad')
                }
              >
                {[
                  { value: 'design', label: 'Проектный вид' },
                  {
                    value: 'cad',
                    label:
                      project.import_status?.mode === 'autocad_live'
                        ? 'Геометрия AutoCAD'
                        : 'Исходный DXF',
                  },
                ].map((option) => (
                  <MenuRadioItem
                    closeOnClick
                    key={option.value}
                    value={option.value}
                    className="rounded-control flex h-8 cursor-pointer items-center justify-between gap-4 px-3 outline-none data-[highlighted]:bg-neutral-100"
                  >
                    {option.label}
                    <Check
                      aria-hidden="true"
                      className={`size-4 ${mapRenderMode === option.value ? '' : 'invisible'}`}
                    />
                  </MenuRadioItem>
                ))}
              </MenuRadioGroup>
            </MenuGroup>
          </MenuPopup>
        </Menu>
      )}
      {!sceneOpen && (
        <MapLegend
          layers={project.layers ?? []}
          embedded
          disabled={mapRenderMode !== 'design'}
        />
      )}
      {!sceneOpen && (
        <MapPlanComparison
          embedded
          mode={planComparison.mode}
          canEnter={Boolean(
            project.plan && project.map_ready && planComparison.canEnter,
          )}
          hasDraft={hasUnsavedWork}
          onChange={planComparison.change}
        />
      )}
      {project.map_ready && (
        <>
          <IconButton
            icon={Clock3}
            variant="ghost"
            controlSize="compact"
            label="Прогноз роста"
            aria-pressed={growthScenario?.open ?? false}
            className="aria-pressed:bg-blue-100 aria-pressed:text-blue-700"
            disabled={!project.plan?.objects?.length || planComparison.active}
            onClick={growthScenario?.toggle}
          />
          {!sceneOpen && <Divider orientation="vertical" />}
          <MapViewSwitch
            embedded
            disabled3d={planComparison.active}
            mode={sceneOpen ? '3d' : '2d'}
            onChange={(mode) => {
              if (mode === '3d' && hasUnsavedWork) {
                setPendingScene(true);
                return;
              }
              changeMapMode(mode);
            }}
          />
        </>
      )}
    </>
  );
  return embedded ? (
    controls
  ) : (
    <CanvasControlSlot position="topRight">
      <CanvasControlGroup
        label="Отображение карты"
        className="relative cursor-default"
      >
        {controls}
      </CanvasControlGroup>
    </CanvasControlSlot>
  );
};
