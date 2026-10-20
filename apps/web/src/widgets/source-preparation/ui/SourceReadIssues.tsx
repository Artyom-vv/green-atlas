import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Button,
  ActionRow,
  DataTable,
  Dialog,
  Disclosure,
  InlineMessage,
  Progress,
} from '@green/ui';
import { preparationApi } from '@/features/source-preparation/api/preparationApi';

const TYPE_NAMES: Record<string, string> = {
  HATCH: 'Штриховка',
  INSERT: 'Вставка или подоснова',
  LWPOLYLINE: 'Полилиния',
};
export function SourceReadIssues({
  projectId,
  sourceSha,
}: {
  projectId: string;
  sourceSha: string;
}) {
  const [open, setOpen] = useState(false);
  const query = useQuery({
    queryKey: ['source-read-issues', projectId, sourceSha],
    queryFn: () => preparationApi.getSourceReadIssues(projectId),
    enabled: open,
    retry: false,
    staleTime: Infinity,
  });
  return (
    <>
      <ActionRow onClick={() => setOpen(true)}>
        Объекты без расчётной геометрии
      </ActionRow>
      {open && (
        <Dialog
          open
          title="Что не удалось прочитать"
          size="wide"
          onClose={() => setOpen(false)}
        >
          {query.isPending ? (
            <Progress label="Читаем отчёт AutoCAD" />
          ) : query.isError ? (
            <InlineMessage tone="error">
              Отчёт не загрузился{' '}
              <Button variant="ghost" onClick={() => void query.refetch()}>
                Повторить
              </Button>
            </InlineMessage>
          ) : (
            <div className="space-y-3">
              <p className="m-0 text-sm text-neutral-600">
                Это пропуски чтения, а не неподтверждённые слои
              </p>
              {!query.data.items.length ? (
                <p className="m-0 text-sm leading-5 text-neutral-600">
                  Непрочитанных объектов в отчёте нет
                </p>
              ) : (
                <DataTable layout="fixed" className="[&_td]:align-top">
                  <colgroup>
                    <col className="w-1/4" />
                    <col className="w-1/4" />
                    <col className="w-1/2" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th>Объект</th>
                      <th>Слой</th>
                      <th>Причина</th>
                    </tr>
                  </thead>
                  <tbody>
                    {query.data.items.map((item) => (
                      <tr key={item.route}>
                        <td>
                          <div>
                            {TYPE_NAMES[item.entity_type] ?? item.entity_type}
                          </div>
                          <code className="font-mono text-xs leading-4 wrap-anywhere text-neutral-500">
                            {item.route}
                          </code>
                        </td>
                        <td className="wrap-anywhere">{item.layer}</td>
                        <td>
                          <div className="wrap-anywhere">{item.reason}</div>
                          <Disclosure variant="plain" title="Ответ AutoCAD">
                            <code className="font-mono text-xs leading-4 wrap-anywhere">
                              {item.detail}
                            </code>
                          </Disclosure>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </DataTable>
              )}
            </div>
          )}
        </Dialog>
      )}
    </>
  );
}
