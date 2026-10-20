import type { FC } from 'react';
import { Button, FormActions } from '@green/ui';
import type { BrushToolPanelProps } from './BrushToolPanel';
interface BrushFooterProps extends Pick<
  BrushToolPanelProps,
  'loading' | 'applying' | 'preview' | 'onApply' | 'onCancel'
> {
  hasStrokes: boolean;
}
export const BrushFooter: FC<BrushFooterProps> = ({
  loading,
  applying,
  preview,
  onApply,
  onCancel,
  hasStrokes,
}) => (
  <FormActions layout="equal">
    <Button variant="secondary" disabled={applying} onClick={onCancel}>
      Отмена
    </Button>
    {!!hasStrokes && (
      <Button
        variant="primary"
        loading={applying}
        disabled={loading || !preview?.change_set?.can_apply}
        onClick={onApply}
      >
        {preview?.added_count
          ? `Добавить ${preview.added_count}`
          : preview?.removed_count
            ? `Убрать ${preview.removed_count}`
            : 'Применить'}
      </Button>
    )}
  </FormActions>
);
