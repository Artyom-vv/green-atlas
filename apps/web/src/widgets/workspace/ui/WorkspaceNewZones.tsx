import type { WorkspaceNewZonesProps } from './WorkspaceNewZones.props';
import type { FC } from 'react';
import { PlantingZonesPanel } from '@/features/planting-zones/ui/PlantingZonesPanel';
import { errorMessage as message } from '@/shared/errors/errorMessage';
export const WorkspaceNewZones: FC<WorkspaceNewZonesProps> = ({
  inspectorView,
  project,
  draftZones,
  tool,
  createManualPlan,
  setDraftZones,
  editor,
}) => (
  <>
    {inspectorView === 'new-zones' && !project.plan && (
      <PlantingZonesPanel
        assignments={draftZones}
        drawingManual={tool === 'draw_area'}
        saving={createManualPlan.isPending}
        error={
          createManualPlan.error ? message(createManualPlan.error) : undefined
        }
        onRemove={(id) =>
          setDraftZones((current) => current.filter((item) => item.id !== id))
        }
        onSave={() => createManualPlan.mutate()}
        onManual={() => editor.setTool('draw_area')}
        onCancelManual={() => editor.setTool('select')}
      />
    )}
  </>
);
