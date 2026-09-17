import type { Plan } from '@green/api-client';
import type { ReleaseFormValues } from './releaseForm';

export function releaseReadiness(
  plan: Plan,
  form: ReleaseFormValues,
  stale = false,
) {
  const objects = plan.objects ?? [];
  const missingSpecies = objects.filter(
    (object) => !object.species_revision_id,
  ).length;
  const hardErrors = (plan.issues ?? []).filter(
    (issue) => issue.severity === 'error',
  ).length;
  const sourcePending = (plan.issues ?? []).some(
    (issue) => issue.code === 'SOURCE_REVIEW_PENDING',
  );
  const { mode, basis } = form;
  const regulatoryReady =
    basis.pp616_status !== 'pending' &&
    basis.pp1160_status !== 'pending' &&
    Boolean(
      basis.pp616_reference.trim() &&
      basis.pp1160_reference.trim() &&
      basis.confirmed_by.trim(),
    );
  const blockedReasons = [
    ...(!objects.length ? ['Добавьте посадки в план перед выпуском.'] : []),
    ...(mode === 'final' && sourcePending
      ? ['Проверьте исходные данные и выполните расчёт ограничений.']
      : []),
    ...(mode === 'final' && hardErrors ? ['Устраните ошибки размещения.'] : []),
    ...(mode === 'final' && missingSpecies ? ['Назначьте виды посадкам.'] : []),
    ...(mode === 'final' && !regulatoryReady
      ? ['Заполните оба решения, их основания и ответственного за проверку.']
      : []),
    ...(mode === 'final' && stale
      ? ['Проверьте основания для текущей версии плана и геометрии.']
      : []),
  ];
  return {
    missingSpecies,
    hardErrors,
    sourcePending,
    blockedReasons,
    ready: !blockedReasons.length,
  };
}
