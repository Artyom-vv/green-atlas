import type { SourcePreparationState } from '../model/sourcePreparation';
import {
  PreparationStatusPropsFor,
  type PreparationStatusProps,
} from './PreparationStatus.props';
import {
  PreparationActionsPropsFor,
  type PreparationActionsProps,
} from './PreparationActions.props';
import {
  SourceLayerFormPropsFor,
  type SourceLayerFormProps,
} from './SourceLayerForm.props';
export interface SourcePreparationViewProps
  extends
    PreparationStatusProps,
    PreparationActionsProps,
    SourceLayerFormProps {
  projectName: string;
  sourceReviewMessage?: string;
}
export const SourcePreparationViewPropsFor = (
  state: SourcePreparationState,
) => ({
  ...PreparationStatusPropsFor(state),
  ...PreparationActionsPropsFor(state),
  ...SourceLayerFormPropsFor(state),
  sourceReviewMessage: state.sourceReviewMessage,
});
