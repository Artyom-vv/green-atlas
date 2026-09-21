import { useSpeciesCatalogFocus } from '../model/useSpeciesCatalogFocus';
import { useEffect, useState, type FC, type ReactNode } from 'react';
import {
  FormProvider,
  useFormContext,
  useWatch,
  type UseFormReturn,
} from 'react-hook-form';
import type { PlanObject, SpeciesShortlistItem } from '@green/api-client';
import { ControlProvider, InlineMessage, ScrollArea, Surface } from '@green/ui';
import { SpeciesAssignmentCatalog } from './SpeciesAssignmentCatalog';
import {
  useSpeciesAssignmentForm,
  type SpeciesAssignmentFormValues,
} from '../model/useSpeciesAssignmentForm';
import { SpeciesAssignmentDetails } from './SpeciesAssignmentDetails';
import { SpeciesAssignmentSelection } from './SpeciesAssignmentSelection';
import { SpeciesAssignmentFooter } from './SpeciesAssignmentFooter';

export interface SpeciesAssignmentPanelProps {
  header?: ReactNode;
  form?: UseFormReturn<SpeciesAssignmentFormValues>;
  objects: PlanObject[];
  shortlist?: SpeciesShortlistItem[];
  loading?: boolean;
  previewing?: boolean;
  error?: string;
  onAssign: (
    revisionId: string,
    sizeClass: SpeciesAssignmentFormValues['sizeClass'],
  ) => void;
  onCancel: () => void;
  onSelectKind?: (kind: PlanObject['kind']) => void;
  onCatalogModeChange?: (browsing: boolean) => void;
}
const OwnedSpeciesAssignment: FC<SpeciesAssignmentPanelProps> = (props) => {
  const form = useSpeciesAssignmentForm();
  return (
    <FormProvider {...form}>
      <SpeciesAssignmentForm {...props} />
    </FormProvider>
  );
};
export const SpeciesAssignmentPanel: FC<SpeciesAssignmentPanelProps> = (
  props,
) =>
  props.form ? (
    <FormProvider {...props.form}>
      <SpeciesAssignmentForm {...props} />
    </FormProvider>
  ) : (
    <OwnedSpeciesAssignment {...props} />
  );

const SpeciesAssignmentForm: FC<SpeciesAssignmentPanelProps> = ({
  header,
  objects,
  shortlist,
  loading,
  previewing,
  error,
  onAssign,
  onSelectKind,
  onCancel,
  onCatalogModeChange,
}) => {
  const form = useFormContext<SpeciesAssignmentFormValues>();
  const values = useWatch({
    control: form.control,
    compute: (values) => values,
  });
  const [query, setQuery] = useState('');
  const [catalogOpen, setCatalogOpen] = useState(true);
  const selected = shortlist?.find(
    (item) => item.species.id === values.revisionId,
  );
  const mixedKinds = new Set(objects.map((object) => object.kind)).size > 1;
  const browsing = (!selected || catalogOpen) && !mixedKinds;
  const showingDetails = selected && !browsing && !mixedKinds;
  useEffect(
    () => onCatalogModeChange?.(browsing),
    [browsing, onCatalogModeChange],
  );
  const { contentRef, headingRef, prepareDetails, prepareSearch } =
    useSpeciesCatalogFocus(browsing, selected?.species.id);

  return (
    <ControlProvider size="compact">
      <Surface
        title="Назначить породу"
        header={header}
        className="@container/inspector flex min-h-0 flex-1 flex-col rounded-none border-0 text-xs text-neutral-800"
      >
        <div
          className="flex min-h-0 min-w-0 flex-1 flex-col gap-4 p-4"
          ref={contentRef}
        >
          <div className="grid shrink-0 gap-4">
            <SpeciesAssignmentSelection
              objects={objects}
              browsing={browsing}
              mixedKinds={mixedKinds}
              previewing={previewing}
              onSelectKind={
                onSelectKind
                  ? (kind) => {
                      form.setValue('revisionId', '');
                      setCatalogOpen(true);
                      onSelectKind(kind);
                    }
                  : undefined
              }
            />
            {error && <InlineMessage tone="error">{error}</InlineMessage>}
          </div>
          {browsing && !error && (
            <SpeciesAssignmentCatalog
              layout="fill"
              loading={loading}
              shortlist={shortlist}
              value={values.revisionId}
              previewing={previewing}
              query={query}
              onQueryChange={setQuery}
              showSelection={Boolean(selected)}
              onChange={(id) => {
                prepareDetails();
                form.setValue('revisionId', id, { shouldDirty: true });
                setCatalogOpen(false);
              }}
            />
          )}
          {showingDetails && (
            <ScrollArea className="flex-1" contentClassName="p-1">
              <SpeciesAssignmentDetails
                selected={selected}
                previewing={previewing}
                headingRef={headingRef}
                onBrowse={() => {
                  prepareSearch();
                  setCatalogOpen(true);
                }}
              />
            </ScrollArea>
          )}
        </div>
        <footer className="shrink-0 border-t border-neutral-200 p-4">
          <SpeciesAssignmentFooter
            objects={objects}
            previewing={previewing}
            loading={loading}
            error={error}
            onAssign={onAssign}
            onCancel={onCancel}
            showingDetails={Boolean(showingDetails)}
            canAssign={selected?.can_assign === true}
          />
        </footer>
      </Surface>
    </ControlProvider>
  );
};
