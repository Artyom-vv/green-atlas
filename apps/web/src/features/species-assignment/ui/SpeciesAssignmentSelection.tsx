import type { FC } from 'react';
import { Button, FormActions } from '@green/ui';
import type { SpeciesAssignmentPanelProps } from './SpeciesAssignmentPanel';
interface SpeciesAssignmentSelectionProps extends Pick<
  SpeciesAssignmentPanelProps,
  'objects' | 'previewing' | 'onSelectKind'
> {
  browsing: boolean;
  mixedKinds: boolean;
}
export const SpeciesAssignmentSelection: FC<
  SpeciesAssignmentSelectionProps
> = ({ objects, previewing, onSelectKind, mixedKinds }) => {
  const selectionLabel = mixedKinds
    ? 'Посадочных мест'
    : objects[0]?.kind === 'shrub'
      ? 'Кустарников'
      : 'Деревьев';
  return (
    <>
      <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
        <span className="shrink-0 text-xs text-neutral-600">
          {selectionLabel}: <strong>{objects.length}</strong>
        </span>
      </div>
      {mixedKinds && (
        <section className="grid gap-2">
          <p className="m-0 text-xs">Тип посадок</p>
          <FormActions layout="equal" minItemWidth="10rem">
            {(['tree', 'shrub'] as const).map((kind) => (
              <Button
                variant="secondary"
                key={kind}
                disabled={previewing || !onSelectKind}
                onClick={() => onSelectKind?.(kind)}
              >
                {kind === 'tree' ? 'Деревьям' : 'Кустарникам'} (
                {objects.filter((object) => object.kind === kind).length})
              </Button>
            ))}
          </FormActions>
        </section>
      )}
    </>
  );
};
