import { Button, Dialog, FormActions } from '@green/ui';

interface Props {
  open: boolean;
  pending: boolean;
  unknown: boolean;
  hasDraft: boolean;
  preserveOperation: boolean;
  onStay: () => void;
  onLeave: () => void;
}
export function LeaveProjectDialog({
  open,
  pending,
  unknown,
  hasDraft,
  preserveOperation,
  onStay,
  onLeave,
}: Props) {
  const title = unknown
    ? 'Проверьте результат сохранения'
    : pending
      ? 'Дождитесь результата операции'
      : hasDraft
        ? 'Уйти без применения?'
        : 'Подготовка ещё выполняется';
  return (
    <Dialog
      open={open}
      title={title}
      onClose={onStay}
      footer={
        <FormActions layout="equal" minItemWidth="12rem">
          <Button variant="secondary" onClick={onStay}>
            Остаться
          </Button>
          {!pending && (
            <Button variant="primary" onClick={onLeave}>
              {hasDraft ? 'Уйти без применения' : 'Уйти'}
            </Button>
          )}
        </FormActions>
      }
    >
      {unknown ? (
        <p>
          Ответ на запись не получен. Сначала проверьте её результат в проекте.
          Повторная запись не отправляется.
        </p>
      ) : pending ? (
        <p>Запись ещё выполняется. Дождитесь подтверждения перед выходом.</p>
      ) : hasDraft ? (
        <p>
          Неприменённые настройки и выбранные объекты не сохранятся. Уже
          сохранённые решения останутся в проекте.
        </p>
      ) : null}
      {preserveOperation && (
        <p>
          Переход к списку проектов не отменяет подготовку. При закрытии
          приложения она прервётся; её состояние останется в проекте.
        </p>
      )}
    </Dialog>
  );
}
