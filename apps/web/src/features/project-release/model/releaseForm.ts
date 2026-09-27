import type {
  RegulatoryReleaseBasis,
  ReleaseCreateRequest,
} from '@green/api-client';

export interface ReleaseFormValues {
  mode: ReleaseCreateRequest['mode'];
  basis: RegulatoryReleaseBasis;
  sceneHorizon: number;
}

export const releaseFieldLimits = {
  reference: 240,
  reviewer: 160,
  horizon: 40,
} as const;

export const pp616Choices = [
  { value: 'pending', label: 'Нужно определить' },
  { value: 'documented', label: 'Компенсация оформлена' },
  { value: 'not_applicable', label: 'Не применяется' },
] as const satisfies ReadonlyArray<{
  value: RegulatoryReleaseBasis['pp616_status'];
  label: string;
}>;

export const pp1160Choices = [
  { value: 'pending', label: 'Нужно определить' },
  { value: 'documented', label: 'Процедура оформлена' },
  { value: 'not_required', label: 'Не требуется' },
] as const satisfies ReadonlyArray<{
  value: RegulatoryReleaseBasis['pp1160_status'];
  label: string;
}>;

export const horizonLabel = (horizon: number) =>
  horizon === 0 ? 'Сейчас' : `${horizon} лет`;
