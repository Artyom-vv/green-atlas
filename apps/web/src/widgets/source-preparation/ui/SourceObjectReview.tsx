/** Подготовка исходника: разбор объектов и сборка подтверждённых AutoCAD областей */
import { useState, type KeyboardEvent } from 'react';
import { X } from 'lucide-react';
import {
  Button,
  ActionRow,
  IconButton,
  Dialog,
  Disclosure,
  InlineMessage,
  Progress,
  Select,
} from '@green/ui';
import type { Project } from '@green/api-client';
import { errorMessage } from '@/shared/errors/errorMessage';
import {
  LAYER_KIND_LABELS,
  SOURCE_OBJECT_KIND_OPTIONS,
  SOURCE_LINEAR_ENTITY_TYPES,
  sourceObjectKindFromValue,
} from '@/entities/source-data/model/layerKinds';
import { localLayerName } from '@/entities/source-data/model/boundaryDecision';
import { ObjectContextScene } from './ObjectContextScene';
import { ObjectReviewQueue } from './ObjectReviewQueue';
import { useObjectReview } from './useObjectReview';
import { ObjectReviewKeyboardHelp } from './ObjectReviewKeyboardHelp';
import { PreparationReviewActions } from './PreparationReviewActions';
import { PreparationReviewViewport } from './PreparationReviewViewport';

function isReviewCandidate(item: NonNullable<Project['layers']>[number]) {
  if (localLayerName(item).trim() === '0' || item.category === 'mixed_source')
    return (
      item.object_count > 0 || Object.keys(item.entity_types ?? {}).length > 0
    );
  return (
    ['building', 'road', 'restricted'].includes(item.mapped_kind ?? '') &&
    Object.keys(item.entity_types ?? {}).some((type) =>
      ['LINE', 'LWPOLYLINE', 'POLYLINE'].includes(type),
    )
  );
}

/** Группа задач и вход в диалог используют один критерий доступности */
export function canReviewSourceObjects(project: Project): boolean {
  return Boolean(
    project.source_file?.cad_snapshot_provenance?.live_capture &&
    project.layers?.some(isReviewCandidate),
  );
}

export function SourceObjectReview({
  project,
  disabled,
}: {
  project: Project;
  disabled: boolean;
}) {
  const [open, setOpen] = useState(false),
    [layer, setLayer] = useState('');
  if (!canReviewSourceObjects(project)) return null;
  const candidates = (project.layers ?? []).filter(isReviewCandidate);
  return (
    <>
      <ActionRow
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
        Разобрать объекты чертежа
      </ActionRow>
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
    blocked = m.busy;
  const navigate = (event: KeyboardEvent) => {
    if (
      m.selectionBlocked ||
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
        if (!m.closeBlocked) onClose();
      }}
      closeDisabled={m.closeBlocked}
      footer={
        <PreparationReviewActions
          busy={
            m.mutation.isPending || m.check.isPending || m.recover.isPending
          }
          status={
            m.mutation.isPending
              ? 'Сохраняем решение'
              : m.check.isPending
                ? 'AutoCAD проверяет выбранные кривые'
                : m.hasRoleDraft
                  ? 'Назначение ещё не применено'
                  : m.hasDraft
                    ? 'Контур ещё не принят'
                    : 'Сохранённые решения остаются в проекте'
          }
        >
          <Button
            variant="ghost"
            controlSize="default"
            disabled={m.closeBlocked}
            onClick={onClose}
          >
            Закрыть
          </Button>
        </PreparationReviewActions>
      }
    >
      <div className="flex h-full min-h-0 flex-col gap-3" onKeyDown={navigate}>
        <div className="grid shrink-0 grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-3">
          <Select
            aria-label="Слой для разбора объектов"
            value={layer}
            title={layer}
            disabled={m.busy || m.hasDraft}
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
            disabled={
              blocked ||
              m.hasRoleDraft ||
              !m.focus ||
              (!m.assembling && !m.focus.can_join)
            }
            aria-pressed={m.assembling}
            onClick={() => {
              m.setAssembling(!m.assembling);
              m.setChosen(!m.assembling && m.focus?.can_join ? [m.focus] : []);
              m.check.reset();
            }}
          >
            {m.assembling ? 'Завершить выбор' : 'Собрать контур'}
          </Button>
          <ObjectReviewKeyboardHelp />
        </div>
        {m.chosen.length > 0 && !m.busy && (
          <p className="m-0 text-xs text-neutral-600">
            Примите контур или нажмите «Завершить выбор» перед закрытием и
            сменой слоя
          </p>
        )}
        {m.writeError != null && (
          <InlineMessage
            tone="error"
            title={
              m.writeOutcome === 'confirmed'
                ? 'Решение сохранено, обновите данные'
                : m.needsRecovery
                  ? 'Сохранение не подтверждено'
                  : 'Решение не сохранено'
            }
          >
            {errorMessage(m.writeError)}{' '}
            {m.needsRecovery && (
              <Button
                variant="ghost"
                loading={m.recover.isPending}
                onClick={() => m.recover.mutate()}
              >
                Проверить состояние
              </Button>
            )}
          </InlineMessage>
        )}
        {m.recover.isError && (
          <InlineMessage tone="error">
            {errorMessage(m.recover.error)}
          </InlineMessage>
        )}
        <PreparationReviewViewport
          size="fill"
          className="grid grid-cols-[9rem_minmax(0,1fr)] md:grid-cols-[11rem_minmax(0,1fr)]"
        >
          {m.query.data ? (
            <ObjectReviewQueue
              items={m.query.data.items}
              index={m.index}
              total={m.query.data.total}
              offset={m.offset}
              busy={m.selectionBlocked}
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
                Объектов для разбора в этом слое нет
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
                  if (!m.selectionBlocked) m.setInspected(item.route);
                }}
              />
            )}
          </div>
        </PreparationReviewViewport>
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
            <div className="space-y-2 border-0 border-t border-solid border-neutral-200 pt-3">
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
                    loading={
                      m.mutation.isPending && m.mutation.variables === 'group'
                    }
                    onClick={() => m.mutation.mutate('group')}
                  >
                    Принять область
                  </Button>
                )}
              </div>
              {!!m.chosen.length && (
                <div className="flex max-h-16 flex-wrap gap-1 overflow-auto">
                  {m.chosen.map((item) => (
                    <span
                      key={item.route}
                      className="rounded-control inline-flex items-center gap-1 border border-solid border-neutral-200 bg-white pl-2"
                      title={item.layer}
                    >
                      <span className="font-mono text-xs leading-4">
                        {item.source.handle}
                      </span>
                      <IconButton
                        icon={<X />}
                        label={`Убрать кривую ${item.source.handle}`}
                        variant="ghost"
                        controlSize="compact"
                        disabled={m.busy}
                        onClick={() => m.toggle(item)}
                      />
                    </span>
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
                {m.focus.reviewable && (
                  <Select
                    aria-label="Назначение объекта"
                    controlSize="compact"
                    value={m.objectKind ?? ''}
                    disabled={blocked}
                    onChange={(event) => {
                      const next = sourceObjectKindFromValue(
                        event.target.value,
                      );
                      if (next) m.setObjectKind(next);
                    }}
                  >
                    <option value="" disabled>
                      Не назначено
                    </option>
                    {SOURCE_OBJECT_KIND_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </Select>
                )}
                {m.hasRoleDraft && (
                  <>
                    <Button
                      controlSize="compact"
                      variant="primary"
                      disabled={blocked}
                      loading={
                        m.mutation.isPending && m.mutation.variables === 'kind'
                      }
                      onClick={() => m.mutation.mutate('kind')}
                    >
                      Применить
                    </Button>
                    <Button
                      controlSize="compact"
                      variant="ghost"
                      disabled={blocked}
                      onClick={m.discardObjectKind}
                    >
                      Отменить
                    </Button>
                  </>
                )}
                {m.focus.reviewable && (
                  <>
                    <Button
                      controlSize="compact"
                      variant="secondary"
                      disabled={
                        blocked ||
                        m.hasRoleDraft ||
                        m.interpretation === 'linear' ||
                        !SOURCE_LINEAR_ENTITY_TYPES.has(m.focus.entity_type)
                      }
                      title="Сохраняет отступ от выбранной линии"
                      onClick={() => m.mutation.mutate('linear')}
                    >
                      Это линейный элемент
                    </Button>
                    <Button
                      controlSize="compact"
                      variant="ghost"
                      disabled={
                        blocked ||
                        m.hasRoleDraft ||
                        m.interpretation === 'reference'
                      }
                      title="Исключает только выбранное условное обозначение"
                      onClick={() => m.mutation.mutate('reference')}
                    >
                      Это обозначение
                    </Button>
                    {(m.interpretation !== 'area' || m.hasKindOverride) && (
                      <Button
                        controlSize="compact"
                        variant="ghost"
                        disabled={blocked || m.hasRoleDraft}
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
                      loading={
                        m.mutation.isPending &&
                        m.mutation.variables === `remove:${group.id}`
                      }
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
