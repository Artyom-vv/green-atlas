import { useState, type KeyboardEvent } from 'react';
import {
  Button,
  Dialog,
  Disclosure,
  InlineMessage,
  Progress,
  Select,
} from '@green/ui';
import type { Project } from '@green/api-client';
import { errorMessage } from '@/shared/errors/errorMessage';
import { LAYER_KIND_LABELS } from '@/entities/source-data/model/layerKinds';
import { ObjectContextScene } from './ObjectContextScene';
import { ObjectReviewQueue } from './ObjectReviewQueue';
import { useObjectReview } from './useObjectReview';

export function SourceObjectReview({
  project,
  disabled,
}: {
  project: Project;
  disabled: boolean;
}) {
  const [open, setOpen] = useState(false),
    [layer, setLayer] = useState('');
  const candidates = (project.layers ?? []).filter(
    (item) =>
      ['building', 'road', 'restricted'].includes(item.mapped_kind ?? '') &&
      Object.keys(item.entity_types ?? {}).some((type) =>
        ['LINE', 'LWPOLYLINE', 'POLYLINE'].includes(type),
      ),
  );
  if (
    !project.source_file?.cad_snapshot_provenance?.live_capture ||
    !candidates.length
  )
    return null;
  return (
    <>
      <Button
        variant="secondary"
        disabled={disabled}
        onClick={() => {
          if (!layer)
            setLayer(
              candidates.find(
                (item) =>
                  item.mapped_kind === 'building' && !item.geometry_complete,
              )?.source_name ?? candidates[0].source_name,
            );
          setOpen(true);
        }}
      >
        Разобрать линии без площади
      </Button>
      {open && (
        <ObjectReviewDialog
          key={layer}
          project={project}
          layers={candidates}
          layer={layer}
          onLayer={setLayer}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

function ObjectReviewDialog({
  project,
  layers,
  layer,
  onLayer,
  onClose,
}: {
  project: Project;
  layers: NonNullable<Project['layers']>;
  layer: string;
  onLayer: (layer: string) => void;
  onClose: () => void;
}) {
  const m = useObjectReview(project, layer),
    blocked = m.busy || m.mutation.isError;
  const navigate = (event: KeyboardEvent) => {
    if (
      m.busy ||
      /^(INPUT|SELECT|TEXTAREA)$/.test((event.target as HTMLElement).tagName)
    )
      return;
    const count = m.query.data?.items.length ?? 0;
    if (event.ctrlKey || event.metaKey) {
      if (event.key === 'Enter' && m.assembling && m.chosen.length >= 2) {
        event.preventDefault();
        m.check.mutate(m.request);
      }
      return;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      m.chooseIndex(
        Math.max(
          0,
          Math.min(count - 1, m.index + (event.key === 'ArrowDown' ? 1 : -1)),
        ),
      );
    } else if (event.key === 'Home') {
      event.preventDefault();
      m.chooseIndex(0);
    } else if (event.key === 'End') {
      event.preventDefault();
      m.chooseIndex(Math.max(0, count - 1));
    } else if (
      event.key === 'PageDown' &&
      m.offset + count < (m.query.data?.total ?? 0)
    ) {
      event.preventDefault();
      m.page(1);
    } else if (event.key === 'PageUp' && m.offset) {
      event.preventDefault();
      m.page(-1);
    }
  };
  return (
    <Dialog
      open
      title="Проверка геометрии"
      size="wide"
      stableHeight
      onClose={() => {
        if (!m.busy) onClose();
      }}
      footer={
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
          <span role="status" aria-live="polite">
            {m.mutation.isPending
              ? 'Сохраняем решение'
              : m.check.isPending
                ? 'AutoCAD проверяет выбранные кривые'
                : '↑ ↓ Очередь   ← → Окружение   Enter Выбрать   PgUp PgDn Страницы'}
          </span>
          <Button
            variant="ghost"
            controlSize="compact"
            disabled={m.busy}
            onClick={onClose}
          >
            Закрыть
          </Button>
        </div>
      }
    >
      <div className="flex h-full min-h-0 flex-col gap-3" onKeyDown={navigate}>
        <div className="grid shrink-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-3">
          <Select
            aria-label="Слой для разбора объектов"
            value={layer}
            title={layer}
            disabled={m.busy}
            onChange={(e) => onLayer(e.target.value)}
          >
            {layers.map((item) => (
              <option key={item.id} value={item.source_name}>
                {item.source_name.split('|').at(-1)}
              </option>
            ))}
          </Select>
          <Button
            variant={m.assembling ? 'primary' : 'secondary'}
            disabled={blocked || !m.focus}
            aria-pressed={m.assembling}
            onClick={() => {
              m.setAssembling(!m.assembling);
              m.setChosen(!m.assembling && m.focus?.can_join ? [m.focus] : []);
              m.check.reset();
            }}
          >
            {m.assembling ? 'Завершить выбор' : 'Собрать контур'}
          </Button>
        </div>
        {m.mutation.isError && (
          <InlineMessage tone="error" title="Сохранение не подтверждено">
            {errorMessage(m.mutation.error)}{' '}
            <Button
              variant="ghost"
              loading={m.recover.isPending}
              onClick={() => m.recover.mutate()}
            >
              Проверить состояние
            </Button>
          </InlineMessage>
        )}
        {m.recover.isError && (
          <InlineMessage tone="error">
            {errorMessage(m.recover.error)}
          </InlineMessage>
        )}
        <div className="grid min-h-0 flex-1 grid-cols-[9rem_minmax(0,1fr)] overflow-hidden rounded border border-neutral-200 md:grid-cols-[11rem_minmax(0,1fr)]">
          {m.query.data ? (
            <ObjectReviewQueue
              items={m.query.data.items}
              index={m.index}
              total={m.query.data.total}
              offset={m.offset}
              busy={m.busy}
              onIndex={m.chooseIndex}
              onPage={m.page}
            />
          ) : (
            <div className="p-3">
              {m.query.isError ? (
                <Button variant="ghost" onClick={() => void m.query.refetch()}>
                  Повторить загрузку
                </Button>
              ) : (
                <Progress label="Загружаем очередь" />
              )}
            </div>
          )}
          <div className="relative min-h-0 min-w-0">
            {m.context.isFetching && m.context.data && (
              <span
                role="status"
                className="pointer-events-none absolute top-2 left-2 z-10 rounded bg-white/95 px-2 py-1 text-xs text-neutral-600"
              >
                Обновляем окружение
              </span>
            )}
            {!m.queueItem ? (
              <div className="grid h-full place-items-center text-sm text-neutral-500">
                Открытых линий в этом слое нет
              </div>
            ) : m.context.isPending ? (
              <div className="grid h-full place-items-center">
                <Progress label="Загружаем окружение" />
              </div>
            ) : m.context.isError ? (
              <InlineMessage tone="error">
                Окружение не загрузилось{' '}
                <Button
                  variant="ghost"
                  onClick={() => void m.context.refetch()}
                >
                  Повторить
                </Button>
              </InlineMessage>
            ) : (
              <ObjectContextScene
                key={`${m.queueItem.route}:${m.scale}`}
                data={m.context.data!}
                active={m.focus?.route}
                chosen={new Set(m.chosen.map((item) => item.route))}
                assembling={m.assembling}
                onPick={m.toggle}
                onInspect={(item) => {
                  if (!m.busy) m.setInspected(item.route);
                }}
              />
            )}
          </div>
        </div>
        <div className="shrink-0 space-y-2">
          <div className="flex min-w-0 items-center justify-between gap-3 text-xs">
            <div className="min-w-0 truncate" title={m.focus?.layer}>
              <strong>{m.focus?.source.handle ?? 'Объект не выбран'}</strong>
              {m.focus && ` — ${m.focus.layer.split('|').at(-1)}`}
            </div>
            <Button
              variant="ghost"
              controlSize="compact"
              disabled={!m.queueItem || m.context.isFetching || m.scale >= 8}
              onClick={() => m.setScale(Math.min(8, m.scale * 2))}
            >
              Больше окружения
            </Button>
          </div>
          {m.assembling ? (
            <div className="space-y-2 rounded border border-neutral-200 bg-neutral-50 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="mr-auto text-xs">
                  Выбрано {m.chosen.length} — нажимайте на кривые
                </span>
                <Select
                  aria-label="Назначение объединённой области"
                  controlSize="compact"
                  value={m.kind}
                  disabled={m.busy}
                  onChange={(e) => m.setKind(e.target.value as typeof m.kind)}
                >
                  <option value="building">Здание</option>
                  <option value="road">Дорога / проезд</option>
                  <option value="restricted">Техническая зона</option>
                </Select>
                <Button
                  controlSize="compact"
                  disabled={
                    blocked || m.chosen.length < 2 || m.chosen.length > 256
                  }
                  loading={m.check.isPending}
                  onClick={() => m.check.mutate(m.request)}
                >
                  Проверить в AutoCAD
                </Button>
                {m.checked?.valid && (
                  <Button
                    controlSize="compact"
                    variant="primary"
                    disabled={blocked}
                    loading={m.mutation.isPending}
                    onClick={() => m.mutation.mutate('group')}
                  >
                    Принять область
                  </Button>
                )}
              </div>
              {!!m.chosen.length && (
                <div className="flex max-h-16 flex-wrap gap-1 overflow-auto">
                  {m.chosen.map((item) => (
                    <Button
                      key={item.route}
                      controlSize="compact"
                      variant="ghost"
                      disabled={m.busy}
                      title={item.layer}
                      onClick={() => m.toggle(item)}
                    >
                      {item.source.handle} ×
                    </Button>
                  ))}
                </div>
              )}
              {m.check.isError && (
                <InlineMessage tone="error">
                  {errorMessage(m.check.error)}
                </InlineMessage>
              )}
              {m.checked && (
                <InlineMessage tone={m.checked.valid ? 'info' : 'warning'}>
                  {m.checked.reason}
                </InlineMessage>
              )}
            </div>
          ) : (
            m.focus && (
              <div className="flex flex-wrap items-center gap-2">
                <span className="mr-auto text-xs text-neutral-500">
                  {LAYER_KIND_LABELS[
                    m.focus.kind as keyof typeof LAYER_KIND_LABELS
                  ] ?? m.focus.kind}
                </span>
                {m.focus.reviewable && (
                  <>
                    <Button
                      controlSize="compact"
                      variant="secondary"
                      disabled={blocked || m.interpretation === 'linear'}
                      title="Сохраняет отступ от выбранной линии"
                      onClick={() => m.mutation.mutate('linear')}
                    >
                      Это линейный элемент
                    </Button>
                    <Button
                      controlSize="compact"
                      variant="ghost"
                      disabled={blocked || m.interpretation === 'reference'}
                      title="Исключает только выбранное условное обозначение"
                      onClick={() => m.mutation.mutate('reference')}
                    >
                      Это обозначение
                    </Button>
                    {m.interpretation !== 'area' && (
                      <Button
                        controlSize="compact"
                        variant="ghost"
                        disabled={blocked}
                        onClick={() => m.mutation.mutate('area')}
                      >
                        Отменить решение
                      </Button>
                    )}
                  </>
                )}
              </div>
            )
          )}
          {!!project.source_file?.area_groups?.length && (
            <Disclosure
              variant="plain"
              title={`Подтверждённые объединения (${project.source_file.area_groups.length})`}
            >
              <div className="max-h-24 space-y-1 overflow-y-auto">
                {project.source_file.area_groups.map((group, i) => (
                  <div
                    key={group.id}
                    className="flex items-center justify-between text-xs"
                  >
                    <span>
                      {LAYER_KIND_LABELS[group.kind]} {i + 1} —{' '}
                      {group.members.length} кривых
                    </span>
                    <Button
                      controlSize="compact"
                      variant="ghost"
                      disabled={blocked}
                      onClick={() => m.mutation.mutate(`remove:${group.id}`)}
                    >
                      Отменить объединение
                    </Button>
                  </div>
                ))}
              </div>
            </Disclosure>
          )}
        </div>
      </div>
    </Dialog>
  );
}
