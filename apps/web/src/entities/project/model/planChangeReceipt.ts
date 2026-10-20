import type {
  ChangeSetPreview,
  PlanChangeReceipt,
  PlanChangeReceiptResult,
} from '@green/api-client';

/** A historical receipt proves only the exact captured operation. */
export function requirePlanChangeReceipt(
  projectId: string,
  preview: ChangeSetPreview,
  baseStateVersion: number,
  result: PlanChangeReceiptResult,
): PlanChangeReceipt {
  const receipt = result.receipt;
  if (result.status === 'not_found' || !receipt)
    throw new Error(
      'Подтверждение пока не найдено. Повторите проверку результата; запись повторно не отправляется.',
    );
  if (
    receipt.project_id !== projectId ||
    receipt.change_set_id !== preview.id ||
    receipt.digest !== preview.digest ||
    receipt.base_plan_version !== preview.base_plan_version ||
    receipt.base_state_version !== baseStateVersion ||
    receipt.status !== result.status ||
    !Number.isSafeInteger(receipt.state_version) ||
    receipt.state_version <= baseStateVersion ||
    !Number.isSafeInteger(receipt.plan_version) ||
    receipt.plan_version <= preview.base_plan_version
  )
    throw new Error(
      'Подтверждение не соответствует отправленному изменению. Результат записи не подтверждён.',
    );
  return receipt;
}
