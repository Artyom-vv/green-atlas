import type { FC } from 'react';
import type { FillPatternRequest, SpeciesRevision } from '@green/api-client';
import { Field, NumberInput, Select } from '@green/ui';
import { SpeciesPicker } from './SpeciesPicker';

export type PlantingComposition = NonNullable<
  FillPatternRequest['composition']
>;
export interface PlantingCompositionFieldsProps {
  composition: PlantingComposition;
  onCompositionChange: (composition: PlantingComposition) => void;
  species: SpeciesRevision[];
  treeSpeciesId?: string;
  onTreeSpeciesChange: (id: string) => void;
  shrubSpeciesId?: string;
  onShrubSpeciesChange: (id: string) => void;
  treeSharePercent?: number;
  onTreeShareChange?: (percent: number) => void;
  allowMixed?: boolean;
  showSpecies?: boolean;
  disabled?: boolean;
  treeSpeciesDisabled?: boolean;
  shrubSpeciesDisabled?: boolean;
  onBrowseTree?: () => void;
  onBrowseShrub?: () => void;
  labels?: Partial<
    Record<
      | 'composition'
      | 'compositionAria'
      | 'tree'
      | 'treeAria'
      | 'shrub'
      | 'shrubAria'
      | 'treeShare'
      | 'treeShareAria',
      string
    >
  >;
}

/** The caller owns values, side effects of composition changes and catalog return. */
export const PlantingCompositionFields: FC<PlantingCompositionFieldsProps> = ({
  composition,
  onCompositionChange,
  species,
  treeSpeciesId,
  onTreeSpeciesChange,
  shrubSpeciesId,
  onShrubSpeciesChange,
  treeSharePercent,
  onTreeShareChange,
  allowMixed = true,
  showSpecies = true,
  disabled,
  treeSpeciesDisabled,
  shrubSpeciesDisabled,
  onBrowseTree,
  onBrowseShrub,
  labels = {},
}) => (
  <>
    <Field label={labels.composition ?? 'Состав'}>
      <Select
        aria-label={labels.compositionAria ?? 'Состав посадок'}
        value={composition}
        disabled={disabled}
        onChange={(event) =>
          onCompositionChange(event.target.value as PlantingComposition)
        }
      >
        <option value="trees">Деревья</option>
        <option value="shrubs">Кустарники</option>
        {allowMixed && <option value="mixed">Смешанный</option>}
      </Select>
    </Field>
    {showSpecies && composition !== 'shrubs' && (
      <Field className="col-span-full" label={labels.tree ?? 'Деревья'}>
        <SpeciesPicker
          label={labels.treeAria ?? 'Порода деревьев'}
          species={species.filter((item) => item.kind === 'tree')}
          value={treeSpeciesId}
          onChange={onTreeSpeciesChange}
          disabled={disabled || treeSpeciesDisabled}
          onBrowse={onBrowseTree}
        />
      </Field>
    )}
    {showSpecies && composition !== 'trees' && (
      <Field className="col-span-full" label={labels.shrub ?? 'Кустарники'}>
        <SpeciesPicker
          label={labels.shrubAria ?? 'Порода кустарников'}
          species={species.filter((item) => item.kind === 'shrub')}
          value={shrubSpeciesId}
          onChange={onShrubSpeciesChange}
          disabled={disabled || shrubSpeciesDisabled}
          onBrowse={onBrowseShrub}
        />
      </Field>
    )}
    {composition === 'mixed' &&
      treeSharePercent !== undefined &&
      onTreeShareChange && (
        <Field label={labels.treeShare ?? 'Деревья, %'}>
          <NumberInput
            aria-label={labels.treeShareAria ?? 'Доля деревьев'}
            value={treeSharePercent}
            onValueChange={onTreeShareChange}
            min={0}
            max={100}
            step={10}
            disabled={disabled}
          />
        </Field>
      )}
  </>
);
