import type { Project } from '@green/api-client';
export type AreaProposal = NonNullable<
  NonNullable<Project['source_file']>['native_area_proposals']
>[number];
export const formatArea = (value: number) =>
  `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(value)} м²`;
export const formatGap = (value: number) =>
  `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(value * 1000)} мм`;
