import type { FC } from 'react';
import { useFormContext } from 'react-hook-form';
import type { SpeciesRevision } from '@green/api-client';
import { FieldGrid } from '@green/ui';
import { PlantingCompositionFields } from '@/entities/species';
import type { PatternFormValues, PatternMode } from '../model/patternForm';
interface PatternCompositionProps {
  values: PatternFormValues;
  species: SpeciesRevision[];
  mode: PatternMode;
  guided: boolean;
  disabled?: boolean;
  shortlistLoading?: boolean;
  onBrowse: (target: 'primary' | 'shrub') => void;
}
export const PatternComposition: FC<PatternCompositionProps> = ({
  values,
  species,
  mode,
  guided,
  disabled,
  shortlistLoading,
  onBrowse,
}) => {
  const { setValue } = useFormContext<PatternFormValues>();
  return (
    <FieldGrid minWidth={140} className="gap-3">
      <PlantingCompositionFields
        composition={values.composition}
        allowMixed={mode === 'fill'}
        onCompositionChange={(value) => {
          setValue('composition', value, { shouldDirty: true });
          setValue('spacing', value === 'shrubs' ? 2 : 6, {
            shouldDirty: true,
          });
        }}
        species={species}
        treeSpeciesId={values.treeSpeciesId}
        onTreeSpeciesChange={(id) =>
          setValue('treeSpeciesId', id, { shouldDirty: true })
        }
        shrubSpeciesId={values.shrubSpeciesId}
        onShrubSpeciesChange={(id) =>
          setValue('shrubSpeciesId', id, { shouldDirty: true })
        }
        disabled={disabled}
        treeSpeciesDisabled={shortlistLoading}
        shrubSpeciesDisabled={shortlistLoading}
        onBrowseTree={guided ? () => onBrowse('primary') : undefined}
        onBrowseShrub={
          guided
            ? () =>
                onBrowse(values.composition === 'shrubs' ? 'primary' : 'shrub')
            : undefined
        }
        labels={{
          compositionAria: 'Состав группы',
          tree: 'Порода',
          treeAria: 'Порода для участка',
          shrub: values.composition === 'shrubs' ? 'Порода' : 'Кустарник',
          shrubAria:
            values.composition === 'shrubs'
              ? 'Порода для участка'
              : 'Порода кустарника',
        }}
      />
    </FieldGrid>
  );
};
