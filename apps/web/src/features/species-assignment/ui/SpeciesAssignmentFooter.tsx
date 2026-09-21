import type { FC } from 'react';
import { useFormContext } from 'react-hook-form';
import { Button, FormActions } from '@green/ui';
import type { SpeciesAssignmentFormValues } from '../model/useSpeciesAssignmentForm';
import type { SpeciesAssignmentPanelProps } from './SpeciesAssignmentPanel';
interface SpeciesAssignmentFooterProps extends Pick<
  SpeciesAssignmentPanelProps,
  'objects' | 'previewing' | 'loading' | 'error' | 'onAssign' | 'onCancel'
> {
  showingDetails: boolean;
  canAssign: boolean;
}
export const SpeciesAssignmentFooter: FC<SpeciesAssignmentFooterProps> = ({
  objects,
  previewing,
  loading,
  error,
  onAssign,
  onCancel,
  showingDetails,
  canAssign,
}) => {
  const form = useFormContext<SpeciesAssignmentFormValues>();
  return (
    <FormActions layout="equal" minItemWidth="10rem">
      <Button variant="secondary" disabled={previewing} onClick={onCancel}>
        Отмена
      </Button>
      {showingDetails && (
        <Button
          variant="primary"
          loading={previewing}
          disabled={
            !canAssign ||
            loading ||
            Boolean(error) ||
            objects.some((object) => object.locked)
          }
          onClick={() => {
            const draft = form.getValues();
            onAssign(draft.revisionId, draft.sizeClass);
          }}
        >
          Проверить замену
        </Button>
      )}
    </FormActions>
  );
};
