import { useMemo, type FC } from 'react';
import {
  FormProvider,
  useFormContext,
  useWatch,
  type UseFormReturn,
} from 'react-hook-form';
import type {
  BrushPreview,
  BrushStroke,
  PlantingZoneAssignment,
  SpeciesRevision,
} from '@green/api-client';
import { InlineMessage } from '@green/ui';
import { PlantingZonePicker } from '@/entities/planting-zone';
import { PlacementToolSurface } from '@/shared/ui/TaskSurface';
import {
  buildBrushDraft,
  hasBrushSpecies,
  type BrushDraft,
  type BrushFormValues,
} from '../model/brushForm';
import { useBrushForm } from '../model/useBrushForm';
import { useBrushPreviewSchedule } from '../model/useBrushPreviewSchedule';
import { BrushParameters } from './BrushParameters';
import { BrushFooter } from './BrushFooter';
import { BrushGuide } from './BrushGuide';
import { BrushResult } from './BrushResult';

export interface BrushToolPanelProps {
  form?: UseFormReturn<BrushFormValues>;
  species?: SpeciesRevision[];
  strokes: BrushStroke[];
  zones: PlantingZoneAssignment[];
  zoneIds: string[];
  width: number;
  operation: BrushStroke['mode'];
  preview?: BrushPreview;
  loading?: boolean;
  applying?: boolean;
  error?: string;
  onZoneIdsChange: (ids: string[]) => void;
  onWidth: (width: number) => void;
  onOperation: (operation: BrushStroke['mode']) => void;
  onPreview: (draft: BrushDraft) => void;
  onApply: () => void;
  onClear: () => void;
  onCancel: () => void;
}

const OwnedBrushTool: FC<BrushToolPanelProps> = (props) => {
  const form = useBrushForm();
  return (
    <FormProvider {...form}>
      <BrushForm {...props} />
    </FormProvider>
  );
};

export const BrushToolPanel: FC<BrushToolPanelProps> = (props) =>
  props.form ? (
    <FormProvider {...props.form}>
      <BrushForm {...props} />
    </FormProvider>
  ) : (
    <OwnedBrushTool {...props} />
  );

const BrushForm: FC<BrushToolPanelProps> = ({
  species,
  strokes,
  zones,
  zoneIds,
  width,
  operation,
  preview,
  loading,
  applying = false,
  error,
  onZoneIdsChange,
  onWidth,
  onOperation,
  onPreview,
  onApply,
  onClear,
  onCancel,
}) => {
  const { control } = useFormContext<BrushFormValues>();
  const values = useWatch({ control, compute: (values) => values });
  const additions = strokes.filter((stroke) => stroke.mode === 'add').length;
  const hasPlantingSettings = operation === 'add' || additions > 0;
  const requireSpecies = Boolean(species);
  const needsSpecies =
    requireSpecies && !hasBrushSpecies(values) && hasPlantingSettings;
  const draft = useMemo(
    () =>
      buildBrushDraft(values, {
        strokes,
        zoneIds,
        width,
        requireSpecies,
      }),
    [values, strokes, zoneIds, width, requireSpecies],
  );
  useBrushPreviewSchedule(draft, applying, onPreview);

  return (
    <PlacementToolSurface
      title="Кисть посадок"
      label="Настройки кисти"
      footer={
        <BrushFooter
          loading={loading}
          applying={applying}
          preview={preview}
          onApply={onApply}
          onCancel={onCancel}
          hasStrokes={strokes.length > 0}
        />
      }
    >
      <PlantingZonePicker
        zones={zones}
        selectedIds={zoneIds}
        disabled={applying}
        onChange={onZoneIdsChange}
      />
      <BrushGuide
        hasZones={zoneIds.length > 0}
        needsSpecies={needsSpecies}
        operation={operation}
        hasStrokes={strokes.length > 0}
        hasPreview={Boolean(preview)}
        loading={loading}
      />
      <BrushParameters
        values={values}
        species={species}
        width={width}
        operation={operation}
        disabled={applying || !zoneIds.length}
        hasPlantingSettings={hasPlantingSettings}
        onWidth={onWidth}
        onOperation={onOperation}
      />
      {!!strokes.length && (
        <BrushResult
          strokes={strokes}
          preview={preview}
          loading={loading}
          applying={applying}
          onClear={onClear}
        />
      )}
      {error && <InlineMessage tone="error">{error}</InlineMessage>}
    </PlacementToolSurface>
  );
};
