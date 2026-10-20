import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ActionRow,
  Button,
  Dialog,
  InlineMessage,
  Progress,
  Select,
} from '@green/ui';
import type { Project } from '@green/api-client';
import { preparationApi } from '@/features/source-preparation/api/preparationApi';
import { useProjectWorkContribution } from '@/features/project-navigation/model/ProjectWorkContext';
import { matchesNativeFaceDecision } from '@/features/source-preparation/model/sourceDecisionProof';
import { useProjectWrite } from '@/entities/project/model/useProjectWrite';
import { errorMessage } from '@/shared/errors/errorMessage';
import { ObjectContextScene } from './ObjectContextScene';
import { formatArea } from './nativeAreaPresentation';
import { PreparationReviewActions } from './PreparationReviewActions';
import { PreparationReviewViewport } from './PreparationReviewViewport';

export function NativeFaceReview({
  project,
  disabled = false,
}: {
  project: Project;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  // Old captures keep their previous review flow until a new native capture.
  if (!project.source_file?.native_session) return null;
  return (
    <>
      <ActionRow disabled={disabled} onClick={() => setOpen(true)}>
        Собранные области
      </ActionRow>
      {open && <FaceDialog project={project} onClose={() => setOpen(false)} />}
    </>
  );
}

function FaceDialog({
  project,
  onClose,
}: {
  project: Project;
  onClose: () => void;
}) {
  const id = project.id!,
    client = useQueryClient();
  const [index, setIndex] = useState(0),
    [layer, setLayer] = useState('');
  const query = useQuery({
    queryKey: ['native-faces', id, project.geometry_version],
    queryFn: () => preparationApi.getNativeFaces(id),
    retry: false,
    staleTime: Infinity,
  });
  const items = (query.data?.items ?? [])
    .filter((item) => !layer || item.layer === layer)
    .sort((a, b) => b.area_m2 - a.area_m2);
  const item = items[Math.min(index, Math.max(0, items.length - 1))];
  const context = useQuery({
    queryKey: [
      'source-object-context',
      id,
      project.geometry_version,
      item?.key,
      2,
    ],
    queryFn: () =>
      preparationApi.getSourceObjectContext(id, `native-face:${item!.key}`, 2),
    enabled: !!item,
    retry: false,
    staleTime: Infinity,
  });
  const write = useProjectWrite(project, (updated) => {
    client.setQueryData(['setup-project', id], updated);
    for (const key of [
      'native-faces',
      'workspace-project',
      'data-passport',
      'source-object-review',
      'source-object-context',
    ])
      void client.invalidateQueries({ queryKey: [key, id] });
  });
  const decision = useMutation({
    mutationFn: () => {
      if (
        !item ||
        !query.data ||
        query.data?.source_sha256 !== project.source_file?.content_sha256
      )
        throw new Error('Исходные данные изменились');
      const request = {
        source_sha256: query.data.source_sha256,
        key: item.key,
        rejected: item.status !== 'rejected',
      };
      return write.execute({
        send: () =>
          preparationApi.decideNativeFace(id, request, {
            expectedStateVersion: project.state_version ?? 0,
          }),
        matches: (fresh) => matchesNativeFaceDecision(fresh, request),
      });
    },
    retry: false,
  });
  const recovery = useMutation({
    mutationFn: write.recover,
    onSuccess: () => decision.reset(),
    retry: false,
  });
  const busy = decision.isPending || recovery.isPending || write.blocked;
  const closeBlocked =
    decision.isPending ||
    write.writeOutcome === 'writing' ||
    write.writeOutcome === 'unknown';
  useProjectWorkContribution(`source-faces:${id}`, {
    writeOutcome: write.writeOutcome,
    readRecovery: write.readRecovery,
    editingBlocked: busy,
  });
  const move = (step: number) => {
    if (!busy)
      setIndex((i) => Math.max(0, Math.min(items.length - 1, i + step)));
  };
  const verified =
    query.data?.source_sha256 === project.source_file?.content_sha256;
  return (
    <Dialog
      open
      title="Собранные области"
      size="wide"
      onClose={() => {
        if (!closeBlocked) onClose();
      }}
      closeDisabled={closeBlocked}
      footer={
        <PreparationReviewActions
          busy={decision.isPending}
          status={
            decision.isPending
              ? 'Сохраняем решение'
              : 'Исходные линии сохраняются'
          }
        >
          <Button
            variant="secondary"
            controlSize="default"
            loading={decision.isPending}
            disabled={
              !item ||
              !verified ||
              busy ||
              query.isFetching ||
              query.isError ||
              item.status === 'overridden' ||
              project.import_status?.editability === 'read_only'
            }
            onClick={() => decision.mutate()}
          >
            {item?.status === 'rejected'
              ? 'Восстановить область'
              : 'Отменить область'}
          </Button>
        </PreparationReviewActions>
      }
    >
      <div
        className="space-y-3"
        onKeyDown={(event) => {
          if ((event.target as HTMLElement).closest('select, input')) return;
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            move(event.key === 'ArrowDown' ? 1 : -1);
          }
        }}
      >
        {query.isPending ? (
          <Progress label="Подготавливаем области в AutoCAD" />
        ) : query.isError ? (
          <InlineMessage tone="error">
            {errorMessage(query.error)}
            <Button variant="ghost" onClick={() => void query.refetch()}>
              Повторить
            </Button>
          </InlineMessage>
        ) : (
          <>
            <Select
              aria-label="Слой собранных областей"
              value={layer}
              disabled={busy}
              onChange={(e) => {
                setLayer(e.target.value);
                setIndex(0);
              }}
            >
              <option value="">Все слои</option>
              {[...new Set(query.data?.items.map((row) => row.layer))].map(
                (name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ),
              )}
            </Select>
            {item ? (
              <>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <dl className="m-0 flex flex-wrap gap-x-6 gap-y-2">
                    <div>
                      <dt className="text-xs leading-4 text-neutral-500">
                        Площадь
                      </dt>
                      <dd className="m-0 font-mono text-sm leading-5 tabular-nums">
                        {formatArea(item.area_m2)}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs leading-4 text-neutral-500">
                        Объекты
                      </dt>
                      <dd className="m-0 font-mono text-sm leading-5 tabular-nums">
                        {item.member_count}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs leading-4 text-neutral-500">
                        Соединения
                      </dt>
                      <dd className="m-0 font-mono text-sm leading-5 tabular-nums">
                        {item.repair_count}
                      </dd>
                    </div>
                  </dl>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="ghost"
                      disabled={!index || busy}
                      onClick={() => move(-1)}
                    >
                      Назад
                    </Button>
                    <span className="font-mono text-xs leading-4 tabular-nums">
                      {index + 1} / {items.length}
                    </span>
                    <Button
                      variant="ghost"
                      disabled={index >= items.length - 1 || busy}
                      onClick={() => move(1)}
                    >
                      Далее
                    </Button>
                  </div>
                </div>
                <PreparationReviewViewport size="context">
                  {context.isPending ? (
                    <div className="p-4">
                      <Progress label="Загружаем окружение" />
                    </div>
                  ) : context.isError ? (
                    <InlineMessage tone="error">
                      {errorMessage(context.error)}
                      <Button
                        variant="ghost"
                        onClick={() => void context.refetch()}
                      >
                        Повторить
                      </Button>
                    </InlineMessage>
                  ) : (
                    <ObjectContextScene
                      key={item.key}
                      data={context.data!}
                      chosen={new Set()}
                      assembling={false}
                      onPick={() => {}}
                      onInspect={() => {}}
                      overlay={item}
                    />
                  )}
                </PreparationReviewViewport>
                <div className="flex flex-wrap gap-4 text-xs leading-4">
                  <span className="text-green-700">
                    Восстановленная площадь
                  </span>
                  <span className="text-warning-strong">
                    Добавленные соединения
                  </span>
                  <span>
                    {item.status === 'rejected'
                      ? 'Область отменена'
                      : item.status === 'overridden'
                        ? 'Изменена другим решением'
                        : 'Учитывается в расчёте'}
                  </span>
                </div>
              </>
            ) : (
              <p className="text-sm text-neutral-600">
                Собранных областей пока нет
              </p>
            )}
          </>
        )}
        {(write.error ?? decision.error) != null && (
          <InlineMessage
            tone="error"
            title={
              write.writeOutcome === 'confirmed'
                ? 'Решение сохранено. Обновите данные'
                : write.readRecovery
                  ? 'Сохранение не подтверждено'
                  : 'Решение не сохранено'
            }
          >
            {errorMessage(write.error ?? decision.error)}
            {write.readRecovery && (
              <Button
                variant="ghost"
                loading={recovery.isPending}
                onClick={() => recovery.mutate()}
              >
                Проверить состояние
              </Button>
            )}
          </InlineMessage>
        )}
      </div>
    </Dialog>
  );
}
