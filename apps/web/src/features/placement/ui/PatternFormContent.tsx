import type { FC } from 'react';
import { Dialog, InlineMessage } from '@green/ui';
import { SpeciesCatalog } from '@/entities/species';
import { catalogItemStatus } from '@/entities/species/model/catalogItems';
import type { usePatternWorkflow } from '../model/usePatternWorkflow';
import type { PatternToolPanelProps } from './PatternToolPanel';
import { PatternResult } from './PatternResult';
import { PatternFormFields } from './PatternFormFields';
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
        <Dialog
          open
          size="wide"
          stableHeight
          title={
            catalog === 'shrub' ? 'Каталог кустарников' : 'Каталог растений'
          }
          onClose={() => workflow.setCatalog(undefined)}
        >
          <SpeciesCatalog
            layout="fill"
            species={catalog === 'shrub' ? shrubs : availableSpecies}
            value={catalog === 'shrub' ? values.shrubSpeciesId : speciesId}
            disabled={loading || shortlistLoading}
            loading={shortlistLoading}
            onChange={selectSpecies}
            itemStatuses={
              options.shortlist &&
              Object.fromEntries(
                (options.shortlist ?? []).map((item) => [
                  item.species.id,
                  catalogItemStatus(item),
                ]),
              )
            }
          />
        </Dialog>
      )}
      <div className="grid gap-3">
        {preview && (
          <PatternResult
            preview={preview}
            zones={options.zones}
            selectedZoneIds={options.selectedZoneIds ?? []}
            selectedSpecies={selectedSpecies}
            compactAlternative={compactAlternative}
            growthHorizon={options.growthHorizon}
            onGrowthHorizon={options.onGrowthHorizon}
            onAlternative={selectAlternative}
          />
        )}
        <PatternFormFields options={options} workflow={workflow} />
        {error && <InlineMessage tone="error">{error}</InlineMessage>}
      </div>
    </>
  );
};
