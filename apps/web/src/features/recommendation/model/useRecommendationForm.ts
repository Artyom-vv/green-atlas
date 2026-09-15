import { useForm } from 'react-hook-form';
import {
  createRecommendationFormDefaults,
  type RecommendationFormValues,
} from './recommendationForm';

export function useRecommendationForm(
  initial?: Partial<RecommendationFormValues>,
) {
  return useForm<RecommendationFormValues>({
    defaultValues: createRecommendationFormDefaults(initial),
    shouldUnregister: false,
    mode: 'onChange',
  });
}
