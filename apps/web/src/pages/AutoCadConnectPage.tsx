import { useParams } from 'react-router-dom';
import { Button, Field, InlineMessage, Progress, TextInput } from '@green/ui';
import { ArrowRight, Check, FileText } from 'lucide-react';
import { useCadTransferReview } from '../features/cad-delivery/model/useCadTransferReview';

const statusText = {
  approved: 'Передача разрешена. Продолжите в AutoCAD.',
  denied: 'Передача отклонена. Файлы не загружены.',
  cancelled: 'Передача отменена в AutoCAD.',
  expired: 'Время подтверждения истекло. Начните передачу заново в AutoCAD.',
};

export function AutoCadConnectPage() {
  const { transferId = '' } = useParams();
  const { code, setCode, query, decision } = useCadTransferReview(transferId);
  const status = query.data?.status;
  const error = decision.error ?? query.error;
  const underlays =
    query.data?.manifest.files.filter(
      (file) => file.kind === 'drawing' && file.name !== query.data?.entry,
    ) ?? [];
  return (
    <main className="grid min-h-dvh place-items-center bg-neutral-50 px-5 py-10">
      <section
        className="w-full max-w-120 rounded-xl border border-neutral-200 bg-white p-7 sm:p-9"
        aria-labelledby="connection-title"
      >
        <div className="mb-7 flex items-center gap-3 text-sm text-neutral-500">
          <span>AutoCAD</span>
          <ArrowRight size={16} aria-hidden="true" />
          <span>Green Atlas</span>
        </div>
        <h1
          id="connection-title"
          className="m-0 text-2xl font-semibold text-neutral-900"
        >
          Передать чертёж?
        </h1>
        <p className="mt-3 text-sm leading-6 text-neutral-600">
          Подтвердите только ту передачу, которую вы начали в AutoCAD.
        </p>
        {query.isLoading && <Progress label="Проверяем передачу" />}
        {query.data && (
          <div className="my-7 flex items-center gap-3 border-y border-neutral-200 py-4">
            <FileText
              size={22}
              aria-hidden="true"
              className="shrink-0 text-neutral-500"
            />
            <div className="min-w-0">
              <p className="m-0 text-sm font-medium break-words">
                {query.data.entry}
              </p>
              <p className="mt-1 mb-0 text-xs text-neutral-500">
                {(query.data.total_bytes / 1024 / 1024).toLocaleString(
                  'ru-RU',
                  { maximumFractionDigits: 1 },
                )}{' '}
                МБ
              </p>
            </div>
          </div>
        )}
        {underlays.length > 0 && (
          <details className="mb-6 text-sm text-neutral-600">
            <summary className="cursor-pointer">
              Подосновы: {underlays.length}
            </summary>
            <ul className="mt-3 max-h-40 overflow-auto overscroll-none pl-5">
              {underlays.map((file) => (
                <li key={file.name} className="mb-2 break-words">
                  {file.name}
                </li>
              ))}
            </ul>
          </details>
        )}
        {error && (
          <InlineMessage tone="error">
            {error instanceof Error
              ? error.message
              : 'Не удалось проверить передачу.'}
          </InlineMessage>
        )}
        {status && status !== 'awaiting_approval' ? (
          <div
            role="status"
            className="flex items-start gap-2 text-sm leading-6"
          >
            {status === 'approved' && (
              <Check
                size={18}
                className="mt-1 shrink-0 text-green-700"
                aria-hidden="true"
              />
            )}
            {statusText[status]}
          </div>
        ) : query.data ? (
          <form
            className="mt-6 grid gap-6"
            onSubmit={(event) => {
              event.preventDefault();
              if (/^[A-F0-9]{8}$/.test(code) && !decision.isPending)
                decision.mutate('approve');
            }}
          >
            <Field label="Код из AutoCAD">
              <TextInput
                value={code}
                onChange={(event) =>
                  setCode(
                    event.target.value
                      .toUpperCase()
                      .replace(/[^A-F0-9]/g, '')
                      .slice(0, 8),
                  )
                }
                autoComplete="off"
                spellCheck={false}
                maxLength={8}
                disabled={decision.isPending}
                className="font-mono tracking-widest"
              />
            </Field>
            <div className="flex flex-wrap justify-end gap-3">
              <Button
                type="button"
                disabled={decision.isPending}
                onClick={() => decision.mutate('deny')}
              >
                Не передавать
              </Button>
              <Button
                type="submit"
                variant="primary"
                loading={decision.isPending}
                disabled={!/^[A-F0-9]{8}$/.test(code) || Boolean(query.error)}
              >
                Разрешить
              </Button>
            </div>
          </form>
        ) : query.error ? (
          <Button
            className="mt-5"
            onClick={() => void query.refetch()}
            loading={query.isFetching}
          >
            Повторить
          </Button>
        ) : null}
      </section>
    </main>
  );
}
