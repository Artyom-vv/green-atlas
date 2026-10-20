import type { SourcePreparationState } from '../model/sourcePreparation';
export interface SourceReadOnlyDocumentProps extends Pick<
  SourcePreparationState,
  | 'layers'
  | 'mappings'
  | 'setMappings'
  | 'sourceWarnings'
  | 'downloadSource'
  | 'reviewOnly'
  | 'layerRecognition'
  | 'allSavedLayersConfirmed'
  | 'layerRecognitionQuery'
  | 'retryLayerRecognition'
  | 'projectQuery'
> {
  onPlan: () => void;
  startSourceEditing: (section?: '02' | '03' | '04') => void;
}
export const SourceReadOnlyDocumentPropsFor = (
  state: Pick<
    SourcePreparationState,
    | 'layers'
    | 'mappings'
    | 'setMappings'
    | 'sourceWarnings'
    | 'downloadSource'
    | 'startSourceEditing'
    | 'reviewOnly'
    | 'layerRecognition'
    | 'allSavedLayersConfirmed'
    | 'layerRecognitionQuery'
    | 'retryLayerRecognition'
    | 'projectQuery'
  >,
) => ({
  layers: state.layers,
  mappings: state.mappings,
  setMappings: state.setMappings,
  sourceWarnings: state.sourceWarnings,
  downloadSource: state.downloadSource,
  startSourceEditing: state.startSourceEditing,
  reviewOnly: state.reviewOnly,
  layerRecognition: state.layerRecognition,
  allSavedLayersConfirmed: state.allSavedLayersConfirmed,
  layerRecognitionQuery: state.layerRecognitionQuery,
  retryLayerRecognition: state.retryLayerRecognition,
  projectQuery: state.projectQuery,
});
