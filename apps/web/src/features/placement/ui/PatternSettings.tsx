import type { FC } from 'react';
import { useFormContext } from 'react-hook-form';
import type { SpeciesRevision, PlacementMaskPreset } from '@green/api-client';
import { FieldGroup } from '@green/ui';
import type { PatternFormValues, PatternMode } from '../model/patternForm';
import { PatternComposition } from './PatternComposition';
import { PatternParameters } from './PatternParameters';
import { PlacementScenarioPicker } from './PlacementScenarioPicker';
interface PatternSettingsProps {
  values: PatternFormValues;
  mode: PatternMode;
  guided: boolean;
  step: number;
  disabled?: boolean;
  shortlistLoading?: boolean;
  species: SpeciesRevision[];
  zoneCount: number;
  axisLength: number;
  invalidOffsets: boolean;
  placementMasks?: PlacementMaskPreset[];
  onBrowse: (target: 'primary' | 'shrub') => void;
}
export const PatternSettings: FC<PatternSettingsProps> = ({
  values,
  mode,
  guided,
  step,
  disabled,
  shortlistLoading,
  species,
  zoneCount,
  axisLength,
  invalidOffsets,
  placementMasks,
  onBrowse,
}) => {
  const { setValue } = useFormContext<PatternFormValues>();
  return (
    <>
      <FieldGroup
        legend={mode === 'row' ? 'Посадки' : undefined}
        aria-label="Посадки"
        disabled={disabled}
        hidden={guided && step === 0}
        className="gap-4"
      >
        <div hidden={guided && step !== 1}>
          <PatternComposition
            values={values}
            species={species}
            mode={mode}
            guided={guided}
            disabled={disabled}
            shortlistLoading={shortlistLoading}
            onBrowse={onBrowse}
          />
        </div>
        <section
          className={
            mode === 'row'
              ? 'grid gap-3 border-0 border-t border-solid border-neutral-200 pt-3'
              : undefined
          }
          aria-label={
            mode === 'row' ? 'Геометрия ряда' : 'Параметры размещения'
          }
          hidden={guided && step !== 2}
        >
          {mode === 'row' && (
            <h3 className="m-0 text-xs font-semibold">Геометрия ряда</h3>
          )}
          <PatternParameters
            mode={mode}
            values={values}
            zoneCount={zoneCount}
            axisLength={axisLength}
            invalidOffsets={invalidOffsets}
          />
        </section>
      </FieldGroup>
      {mode === 'fill' && (!guided || step === 2) && (
        <PlacementScenarioPicker
          value={values.placementScenario}
          presets={placementMasks}
          disabled={disabled}
          onChange={(value) =>
            setValue('placementScenario', value, { shouldDirty: true })
          }
        />
      )}
    </>
  );
};
