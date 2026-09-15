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
> = ({ objects, previewing, onSelectKind, browsing, mixedKinds }) => {
  const selectionLabel = mixedKinds
    ? 'Посадочных мест'
    : objects[0]?.kind === 'shrub'
      ? 'Кустарников'
      : 'Деревьев';
  return (
    <>
      {' '}
      <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
        <span className="rounded-control shrink-0 border border-solid border-neutral-200 px-2 py-1 text-xs">
          {selectionLabel}: <strong>{objects.length}</strong>
        </span>
        <p className="m-0 min-w-[min(100%,240px)] flex-1 text-xs leading-5 text-neutral-600">
          {mixedKinds
            ? 'Выберите тип посадок для назначения породы.'
            : browsing
              ? 'Выберите породу, затем проверьте замену в выбранных местах.'
              : 'Выберите посадочный материал и проверьте условия подбора.'}
        </p>
      </div>
      {mixedKinds && (
        <section className="grid gap-2">
          <p className="m-0 text-xs">Кому назначить вид?</p>
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
