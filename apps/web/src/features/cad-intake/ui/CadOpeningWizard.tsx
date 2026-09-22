import { useState } from 'react';
import type { CadPrepareRequest, ProjectOperation } from '@green/api-client';
import { Button, Dialog, Disclosure, InlineMessage } from '@green/ui';
import { Check, FileCheck2 } from 'lucide-react';
import { OperationProgress } from '@/entities/operation/ui/OperationProgress';
import type { useCadPreparation } from '../model/useCadPreparation';

interface Props {
  intake: ProjectOperation;
  prepared: ReturnType<typeof useCadPreparation<CadPrepareRequest>>;
  open: boolean;
  onClose: () => void;
  onReplace: () => void;
  onRetry: () => void;
  onMapping: () => void;
  disabled?: boolean;
}

const steps = ['Файлы', 'Подосновы', 'Геометрия', 'Слои'];
const referenceKey = (owner: string, block: string) =>
  JSON.stringify([owner, block]);

export function CadOpeningWizard({
  intake,
  prepared,
  open,
  onClose,
  onReplace,
  onRetry,
  onMapping,
  disabled,
}: Props) {
  const [step, setStep] = useState(0);
  const passport = intake.cad_intake?.passport;
  if (!passport) return null;
  const entries = passport.entries?.length
    ? passport.entries
    : [passport.entry];
  const rejected = passport.drawings.filter(
    (item) => entries.includes(item.path) && item.status !== 'readable',
  );
  const missing = passport.references.filter(
    (item) => item.status !== 'resolved',
  );
  const skippedFiles = rejected
    .filter((item) => item.path !== passport.entry)
    .map((item) => item.path);
  const unresolved = passport.drawings
    .filter((item) => !skippedFiles.includes(item.path))
    .reduce((sum, item) => sum + (item.inspection?.native_unresolved ?? 0), 0);
  const primaryFailed = !passport.drawings.some(
    (item) => item.path === passport.entry && item.status === 'readable',
  );
  const preparation =
    prepared.operation?.cad_prepare?.request.intake_operation_id === intake.id
      ? prepared.operation
      : undefined;
  const completed = preparation?.status === 'completed';
  const current = completed ? 3 : step;
  const busy = prepared.busy || prepared.opening || Boolean(disabled);
  const ready = current !== 0 || !primaryFailed;
  const next = () => {
    if (!ready || busy || !intake.id) return;
    if (current < 2) setStep(current + 1);
    else
      prepared.launch({
        intake_operation_id: intake.id,
        manifest_sha256: passport.manifest_sha256,
        profile_version: 1,
        opening_review: {
          skipped_drawings: skippedFiles,
          skipped_references: missing.map(({ owner, block }) => ({
            owner,
            block,
          })),
          accept_partial_geometry: unresolved > 0,
        },
      });
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Открытие проекта"
      size="form"
      stableHeight="compact"
      footer={
        <div className="flex items-center gap-3">
          {current > 0 && !completed && (
            <Button disabled={busy} onClick={() => setStep(current - 1)}>
              Назад
            </Button>
          )}
          <div className="ml-auto flex gap-3">
            {completed ? (
              <>
                <Button onClick={prepared.open} loading={prepared.opening}>
                  Открыть план
                </Button>
                <Button
                  variant="primary"
                  disabled={prepared.opening}
                  onClick={onMapping}
                >
                  Сверить слои
                </Button>
              </>
            ) : (
              <Button
                variant="primary"
                disabled={!ready || busy || prepared.loading}
                loading={prepared.busy}
                onClick={next}
              >
                {current === 1 && missing.length
                  ? 'Продолжить с доступными данными'
                  : current === 2
                    ? 'Открыть проект'
                    : 'Далее'}
              </Button>
            )}
          </div>
        </div>
      }
    >
      <div className="flex min-h-0 flex-1 flex-col gap-6">
        <ol
          aria-label="Этапы открытия"
          className="m-0 grid shrink-0 list-none grid-cols-4 gap-3 p-0"
        >
          {steps.map((label, index) => (
            <li
              key={label}
              aria-current={index === current ? 'step' : undefined}
              className={`border-t-2 pt-3 text-sm ${index <= current ? 'border-blue-600 text-blue-700' : 'border-neutral-200 text-neutral-500'}`}
            >
              <span className="flex items-center gap-2">
                {index < current && <Check size={14} aria-hidden="true" />}
                {label}
              </span>
            </li>
          ))}
        </ol>
        <section
          aria-label={steps[current]}
          className="min-h-0 flex-1 space-y-5 overflow-y-auto overscroll-none pr-2"
        >
          {current === 0 && (
            <>
              <h3 className="m-0 text-xl font-semibold">
                {rejected.length ? 'Не все файлы прочитаны' : 'Файлы проверены'}
              </h3>
              <p className="text-sm text-neutral-600">
                {rejected.length
                  ? 'Исправьте комплект или выберите, без каких файлов продолжить.'
                  : 'Исходники остаются неизменными. Далее проверим подосновы и геометрию.'}
              </p>
              {passport.drawings
                .filter((item) => entries.includes(item.path))
                .map((item) => (
                  <div
                    key={item.path}
                    className="grid gap-2 border-b border-neutral-200 pb-4"
                  >
                    <span className="text-sm font-medium break-words">
                      {item.path}
                    </span>
                    {item.status === 'readable' ? (
                      <span className="flex items-center gap-2 text-xs text-neutral-500">
                        <FileCheck2 size={14} />
                        Прочитан AutoCAD
                      </span>
                    ) : (
                      <>
                        <p className="m-0 text-sm text-neutral-600">
                          {item.message ||
                            'Не удалось проверить данные AutoCAD для этого файла.'}
                        </p>
                        {item.path === passport.entry ? (
                          <p className="m-0 text-sm">
                            Выберите другой основной DXF или обновите этот файл
                            и его снимок AutoCAD.
                          </p>
                        ) : (
                          <p className="m-0 text-sm text-neutral-600">
                            Файл будет пропущен. Доступные данные останутся в
                            проекте.
                          </p>
                        )}
                      </>
                    )}
                  </div>
                ))}
              {!!rejected.length && (
                <Button disabled={busy} onClick={onReplace}>
                  Обновить комплект
                </Button>
              )}
            </>
          )}
          {current === 1 && (
            <>
              <h3 className="m-0 text-xl font-semibold">
                {missing.length ? 'Проверьте подосновы' : 'Подосновы проверены'}
              </h3>
              <p className="text-sm text-neutral-600">
                {missing.length
                  ? 'Ненайденная подоснова не участвует в расчёте. Остальные данные можно открыть.'
                  : passport.references.length
                    ? 'Все внешние ссылки найдены в комплекте.'
                    : 'Внешние ссылки не заявлены в снимке AutoCAD.'}
              </p>
              {!!missing.length && (
                <Disclosure title={`Не найдены подосновы: ${missing.length}`}>
                  <div className="grid gap-4 py-3 text-sm text-neutral-600">
                    <div className="max-h-52 space-y-3 overflow-y-auto overscroll-none pr-2">
                      {missing.map((item) => (
                        <div
                          key={referenceKey(item.owner, item.block)}
                          className="grid gap-1 border-b border-neutral-200 pb-3"
                        >
                          <span className="font-medium break-words text-neutral-800">
                            {item.requested_path || item.block}
                          </span>
                          <span className="text-xs break-words text-neutral-500">
                            В чертеже {item.owner}
                          </span>
                        </div>
                      ))}
                    </div>
                    <p className="m-0">
                      В AutoCAD откройте «Внешние ссылки», укажите найденный
                      файл и создайте новый снимок Green Atlas. Затем обновите
                      комплект. Переназначение одного пути в браузере не добавит
                      геометрию в старый снимок.
                    </p>
                    <Button disabled={busy} onClick={onReplace}>
                      Обновить комплект
                    </Button>
                  </div>
                </Disclosure>
              )}
            </>
          )}
          {current === 2 && (
            <>
              <h3 className="m-0 text-xl font-semibold">
                {unresolved
                  ? 'Открыть обработанную часть?'
                  : 'Геометрия готова к открытию'}
              </h3>
              <p className="text-sm text-neutral-600">
                {unresolved
                  ? `Не обработано объектов: ${unresolved.toLocaleString('ru-RU')}. Они не будут учитываться в расчёте. Исходные файлы сохраняются.`
                  : 'Следующим шагом можно сверить назначение слоёв или сразу перейти к плану.'}
              </p>
              {!!unresolved && !prepared.busy && (
                <Button disabled={busy} onClick={onReplace}>
                  Исправить исходные данные
                </Button>
              )}
              {preparation && (
                <OperationProgress
                  operation={preparation}
                  title="Подготовка проекта"
                  onCancel={prepared.cancel}
                  actionBusy={prepared.cancelling}
                />
              )}
              {prepared.error && (
                <InlineMessage tone="error">
                  {prepared.error.message}
                </InlineMessage>
              )}
              {preparation &&
                ['failed', 'interrupted', 'cancelled'].includes(
                  preparation.status,
                ) && (
                  <Button disabled={busy} onClick={onRetry}>
                    Перепроверить комплект
                  </Button>
                )}
            </>
          )}
          {current === 3 && (
            <>
              <h3 className="m-0 text-xl font-semibold">Проект открыт</h3>
              <p className="text-sm text-neutral-600">
                Назначения слоёв заполнены предварительно. Сверьте их перед
                расчётом или продолжите работу с планом.
              </p>
              {(unresolved > 0 ||
                skippedFiles.length > 0 ||
                missing.length > 0) && (
                <p className="text-sm text-neutral-500">
                  Решения и замечания сохранены в исходных данных проекта.
                </p>
              )}
            </>
          )}
        </section>
      </div>
    </Dialog>
  );
}
