/** Общая панель сценария роста над 2D и 3D, независимо от инструмента размещения */
import { GrowthScenarioPanel } from '@/features/growth-scenario/ui/GrowthScenarioPanel';
import { CanvasControlSlot } from '@/shared/ui/CanvasControls';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface CanvasGrowthScenarioProps extends Pick<
  WorkspaceReadyModel,
  'growthHorizon' | 'planComparison' | 'setGrowthHorizon'
> {
  growthScenario?: WorkspaceReadyModel['growthScenario'];
}
export const CanvasGrowthScenarioPropsFor = (
  props: CanvasGrowthScenarioProps,
): CanvasGrowthScenarioProps => ({
  growthHorizon: props.growthHorizon,
  growthScenario: props.growthScenario,
  planComparison: props.planComparison,
  setGrowthHorizon: props.setGrowthHorizon,
});
export function CanvasGrowthScenario({
  growthHorizon,
  growthScenario,
  planComparison,
  setGrowthHorizon,
}: CanvasGrowthScenarioProps) {
  if (!growthScenario?.open || planComparison.active) return null;
  return (
    <CanvasControlSlot position="bottomCenter">
      <GrowthScenarioPanel year={growthHorizon} onYear={setGrowthHorizon} />
    </CanvasControlSlot>
  );
}
