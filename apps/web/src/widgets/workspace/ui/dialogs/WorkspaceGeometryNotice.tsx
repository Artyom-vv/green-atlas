/** Одно уведомление при открытии подготовленного проекта; подробности остаются
 * в паспорте, закрытие не подтверждает данные и не меняет допуск посадок. */
import {
  Button,
  Dialog,
  Disclosure,
  FormActions,
  InlineMessage,
} from '@green/ui';
import { useGeometryNotice } from '@/features/workspace/model/useGeometryNotice';

export function WorkspaceGeometryNotice({
  projectId,
  prepared,
}: {
  projectId: string;
  prepared: boolean;
}) {
  const notice = useGeometryNotice(projectId, prepared);
  if (!notice.open) return null;
  return (
    <Dialog
      open
      title="Есть пропуски геометрии"
      onClose={notice.dismiss}
      footer={
        <FormActions>
          <Button variant="primary" onClick={notice.dismiss}>
            Понятно
          </Button>
        </FormActions>
      }
    >
      <div className="grid gap-4">
        <InlineMessage tone="warning">
          Часть объектов чертежа не удалось подготовить для расчёта
        </InlineMessage>
        <Disclosure title={`Слои с пропусками (${notice.layers.length})`}>
          <ul className="m-0 grid gap-2 pl-5 text-xs wrap-anywhere">
            {notice.layers.map((layer) => (
              <li key={layer}>{layer}</li>
            ))}
          </ul>
        </Disclosure>
      </div>
    </Dialog>
  );
}
