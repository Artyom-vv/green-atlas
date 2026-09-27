import type { FC } from 'react';
import { InlineMessage } from '@green/ui';
import { SpeciesCatalog } from '@/entities/species';
import type { usePatternWorkflow } from '../model/usePatternWorkflow';
import type { PatternToolPanelProps } from './PatternToolPanel';
import { PatternResult } from './PatternResult';
import { PatternFormFields } from './PatternFormFields';
import { SearchDomainProgress } from './SearchDomainProgress';
import { GeometryInspectionPanel } from './GeometryInspectionPanel';
interface PatternFormContentProps {
  options: PatternToolPanelProps;
  workflow: ReturnType<typeof usePatternWorkflow>;
}
export const PatternFormContent: FC<PatternFormContentProps> = ({
  options,
  workflow,
}) => {
  const { preview, error, loading, shortlistLoading } = options;
  const {
    catalog,
    shrubs,
    availableSpecies,
    values,
    speciesId,
    selectSpecies,
    selectedSpecies,
    compactAlternative,
    selectAlternative,
  } = workflow;
  return (
    <>
      {catalog && (
        <section
          className="flex min-h-0 flex-1 flex-col gap-3"
          aria-label="Выбор породы"
        >
          <h3 className="m-0 shrink-0 text-sm font-semibold">
            {catalog === 'shrub' ? 'Порода кустарника' : 'Порода посадок'}
          </h3>
          <SpeciesCatalog
            layout="fill"
            species={catalog === 'shrub' ? shrubs : availableSpecies}
            value={catalog === 'shrub' ? values.shrubSpeciesId : speciesId}
            disabled={loading || shortlistLoading}
            loading={shortlistLoading}
            onChange={selectSpecies}
          />
        </section>
      )}
      <div className="grid gap-3" hidden={Boolean(catalog)}>
        {options.mode !== 'row' &&
          (options.calculating || options.preparation) && (
            <SearchDomainProgress
              preview={options.preparation}
              running={Boolean(options.calculating)}
            />
          )}
        {preview && !options.calculating && (
          <PatternResult
            preview={preview}
            zones={options.zones}
            selectedZoneIds={options.selectedZoneIds ?? []}
            selectedSpecies={selectedSpecies}
            compactAlternative={compactAlternative}
            growthHorizon={options.growthHorizon}
            onGrowthHorizon={options.onGrowthHorizon}
            onAlternative={selectAlternative}
            inspection={options.inspection}
          />
        )}
        {(options.mode === 'row' ||
          (!options.calculating && !options.preparation)) && (
          <PatternFormFields options={options} workflow={workflow} />
        )}
        {!preview && !options.calculating && !options.preparation && workflow.step === 2 && selectedSpecies && (
          <GeometryInspectionPanel inspection={options.inspection} plant={{
            kind: values.composition === 'shrubs' ? 'shrub' : 'tree',
            species_revision_id: speciesId,
            size_class: 'standard',
            spacing_policy: values.spacingPolicy,
          }} />
        )}
        {error && <InlineMessage tone="error">{error}</InlineMessage>}
      </div>
    </>
  );
};
