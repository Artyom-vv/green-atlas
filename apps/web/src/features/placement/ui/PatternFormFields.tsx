import type { FC } from 'react';
import { PlantingZonePicker } from '@/entities/planting-zone';
import type { usePatternWorkflow } from '../model/usePatternWorkflow';
import type { PatternToolPanelProps } from './PatternToolPanel';
import { RowAxisControls } from './RowAxisControls';
import { PatternSettings } from './PatternSettings';
import { RowSketchNotice } from './RowSketchNotice';

interface PatternFormFieldsProps {
  options: PatternToolPanelProps;
  workflow: ReturnType<typeof usePatternWorkflow>;
}
export const PatternFormFields: FC<PatternFormFieldsProps> = ({
  options,
  workflow,
}) => {
  const {
    mode,
    guided = false,
    selectedZoneIds = [],
    drawingZone = false,
    loading,
    preview,
  } = options;
  const { step, values, sketch, canEditSettings, catalogSpecies, setCatalog } =
    workflow;
  return (
    <div className="grid gap-4" hidden={Boolean(preview)}>
      <div hidden={guided && step !== 0}>
        <PlantingZonePicker
          expanded={guided}
          zones={options.zones}
          selectedIds={selectedZoneIds}
          disabled={loading || Boolean(preview)}
          drawing={drawingZone}
          onChange={(ids) => options.onSelectedZoneIdsChange?.(ids)}
          onCreate={mode === 'fill' ? options.onDrawZone : undefined}
          onCancelCreate={options.onCancel}
        />
      </div>
      {mode === 'row' && (
        <RowAxisControls
          axis={options.axis}
          source={options.axisSource}
          mode={options.axisMode ?? 'pick'}
          points={options.axisDrawingPoints ?? 0}
          length={sketch.length}
          loading={loading}
          onModeChange={options.onAxisModeChange}
          onFinish={options.onFinishAxis}
          onFit={options.onFitAxis}
          onReverse={options.onReverseAxis}
        />
      )}
      {drawingZone && (
        <p className="m-0 text-xs leading-4 text-neutral-600">
          Поставьте точки по границе участка и замкните контур
        </p>
      )}
      {!drawingZone && canEditSettings && (
        <PatternSettings
          values={values}
          mode={mode}
          guided={guided}
          step={step}
          disabled={loading || Boolean(preview)}
          shortlistLoading={options.shortlistLoading}
          species={catalogSpecies}
          zoneCount={new Set(selectedZoneIds).size}
          axisLength={sketch.length}
          invalidOffsets={sketch.invalidOffsets}
          placementMasks={options.placementMasks}
          onBrowse={setCatalog}
        />
      )}
      {mode === 'row' && options.axis && options.axisMode !== 'draw' && (
        <RowSketchNotice
          invalidOffsets={sketch.invalidOffsets}
          total={sketch.total}
        />
      )}
    </div>
  );
};
