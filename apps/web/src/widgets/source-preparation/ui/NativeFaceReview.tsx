import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Dialog, InlineMessage, Progress, Select } from '@green/ui';
import type { Project } from '@green/api-client';
import { preparationApi } from '@/features/source-preparation/api/preparationApi';
import { errorMessage } from '@/shared/errors/errorMessage';
import { ObjectContextScene } from './ObjectContextScene';
import { formatArea } from './nativeAreaPresentation';

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
      <Button
        variant="secondary"
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        Собранные области
      </Button>
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
  const items = (query.data?.items ?? []).filter(
    (item) => !layer || item.layer === layer,
  ).sort((a, b) => b.area_m2 - a.area_m2);
  const item = items[Math.min(index, Math.max(0, items.length - 1))];
  const context = useQuery({
    queryKey: [
      'source-object-context',
      id,
      project.geometry_version,
      item?.key,
      2,
    ],
    queryFn: () => preparationApi.getSourceObjectContext(id, `native-face:${item!.key}`, 2),
    enabled: !!item,
    retry: false,
    staleTime: Infinity,
  });
  const decision = useMutation({
    mutationFn: () =>
      preparationApi.decideNativeFace(
        id,
        {
          source_sha256: query.data!.source_sha256,
          key: item!.key,
          rejected: item!.status !== 'rejected',
        },
        { expectedStateVersion: project.state_version ?? 0 },
      ),
    onSuccess: (updated) => {
      client.setQueryData(['setup-project', id], updated);
      for (const key of [
        'native-faces',
        'workspace-project',
        'data-passport',
        'source-object-review',
        'source-object-context',
      ])
        void client.invalidateQueries({ queryKey: [key, id] });
    },
  });
  const move = (step: number) => {
    if (!decision.isPending)
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
        if (!decision.isPending) onClose();
      }}
      footer={
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span role="status" className="text-sm text-neutral-600">
            {decision.isPending
              ? 'Сохраняем решение'
              : 'Исходные линии сохраняются'}
          </span>
          <Button
            variant="secondary"
            loading={decision.isPending}
            disabled={
              !item ||
              !verified ||
              item.status === 'overridden' ||
              project.import_status?.editability === 'read_only'
            }
            onClick={() => decision.mutate()}
          >
            {item?.status === 'rejected'
              ? 'Восстановить область'
              : 'Отменить область'}
          </Button>
        </div>
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
                  <div className="flex gap-6 text-sm">
                    <span>{formatArea(item.area_m2)}</span>
                    <span>Объектов {item.member_count}</span>
                    <span>Соединений {item.repair_count}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="ghost"
                      disabled={!index || decision.isPending}
                      onClick={() => move(-1)}
                    >
                      Назад
                    </Button>
                    <span className="text-sm">
                      {index + 1} / {items.length}
                    </span>
                    <Button
                      variant="ghost"
                      disabled={index >= items.length - 1 || decision.isPending}
                      onClick={() => move(1)}
                    >
                      Далее
                    </Button>
                  </div>
                </div>
                <div className="h-[min(52vh,32rem)] overflow-hidden rounded border border-neutral-200">
                  {context.isPending ? (
                    <div className="p-5">
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
                </div>
                <div className="flex flex-wrap gap-5 text-xs">
                  <span className="text-green-700">
                    Восстановленная площадь
                  </span>
                  <span className="text-amber-700">Добавленные соединения</span>
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
        {decision.isError && (
          <InlineMessage tone="error">
            {errorMessage(decision.error)}
          </InlineMessage>
        )}
      </div>
    </Dialog>
  );
}
