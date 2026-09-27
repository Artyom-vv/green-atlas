import { startPreview } from '../api/startPreview';
import {
  useCadPreparation,
  type CadPreparationOptions,
} from './useCadPreparation';

export function useCadPreview(options: CadPreparationOptions) {
  return useCadPreparation(options, 'prepare_cad_preview', startPreview);
}
