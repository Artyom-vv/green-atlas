import type { SceneRenderJob, SceneRenderPreparation } from '@green/api-client';
import {
  Disclosure,
  IconButton,
  LinkButton,
  Popover,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
} from '@green/ui';
import { Download, Info } from 'lucide-react';
import { sceneRenderFileUrl } from '@/features/scene-render/model/useSceneRender';

/** Сведения принадлежат принятому заданию, а не текущему ракурсу редактора. */
export function SceneRenderDetails({
  job,
  context,
}: {
  job: SceneRenderJob;
  context?: { horizonYear: number; summary: SceneRenderPreparation['summary'] };
}) {
  return (
    <Popover>
      <PopoverTrigger
        render={
          <IconButton
            icon={Info}
            label="Сведения об изображении"
            variant="ghost"
            controlSize="compact"
          />
        }
      />
      <PopoverPopup align="end" size="default">
        <PopoverTitle className="m-0 mb-3 text-sm font-semibold">
          Сведения об изображении
        </PopoverTitle>
        <div className="grid gap-3">
          <dl className="m-0 grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-2 text-sm [&_dd]:m-0 [&_dd]:text-right [&_dd]:tabular-nums [&_dt]:text-neutral-600">
            <dt>Размер</dt>
            <dd>
              {job.width} × {job.height} px
            </dd>
            {context && (
              <>
                <dt>Горизонт роста</dt>
                <dd>
                  {context.horizonYear === 0
                    ? 'Сейчас'
                    : `${context.horizonYear} лет`}
                </dd>
                <dt>Деревья окружения</dt>
                <dd>{context.summary.context_tree_count}</dd>
                <dt>Соседние здания</dt>
                <dd>{context.summary.map_building_count}</dd>
                <dt>Высоты из OSM</dt>
                <dd>{context.summary.osm_building_count}</dd>
                <dt>Условные объёмы</dt>
                <dd>{context.summary.conditional_building_count}</dd>
              </>
            )}
          </dl>
          <Disclosure
            title="Состав и допущения"
            variant="plain"
            controlSize="compact"
          >
            <div className="grid gap-2 text-xs leading-4 text-neutral-600">
              <p className="m-0">
                Окружение, посадки и ракурс зафиксированы при запуске.
              </p>
              <p className="m-0">
                Размеры существующих деревьев и здания без измеренной высоты
                условные. Без рельефа основание плоское.
              </p>
              <p className="m-0">
                Модели Botaniq и материалы иллюстративные. Подмены растений
                перечислены в данных рендера.
              </p>
              {job.message && <p className="m-0 break-words">{job.message}</p>}
            </div>
          </Disclosure>
          {job.receipt_url && (
            <LinkButton
              variant="secondary"
              controlSize="compact"
              startIcon={<Download />}
              href={sceneRenderFileUrl(job.receipt_url)}
              download
            >
              Данные рендера
            </LinkButton>
          )}
        </div>
      </PopoverPopup>
    </Popover>
  );
}
