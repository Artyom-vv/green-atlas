import type {
  LocationBounds,
  LocationPoint,
  LocationSuggestion,
} from '@green/api-client';
import { Button, Dialog, Field, IconButton, TextInput } from '@green/ui';
import {
  ArrowLeft,
  ArrowRight,
  LoaderCircle,
  MapPin,
  Trash2,
  TriangleAlert,
  X,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  boundsOf,
  collectionLines,
  geometryLines,
  projectMapPoint,
  unprojectMapPoint,
} from '../model/locationGeometry';
import { locationLabel } from '../model/locationPresentation';
import { useProjectLocation } from '../model/useProjectLocation';
import { LocationPlan } from './LocationPlan';
import { LocationSearchField } from './LocationSearchField';
import { LocationManualWorkspace, PairPicker } from './LocationManualWorkspace';
import { LocationStreetWorkspace } from './LocationStreetWorkspace';
import { LocationDetails } from './LocationDetails';
import { LocationServiceSettingsForm } from './LocationServiceSettings';

export interface ProjectLocationDialogProps {
  projectId: string;
  open: boolean;
  onClose: () => void;
  onApplied: () => void | Promise<void>;
}
export function ProjectLocationDialog(props: ProjectLocationDialogProps) {
  return props.open ? (
    <LocationSession key={props.projectId} {...props} />
  ) : null;
}
function LocationSession({
  projectId,
  onClose,
  onApplied,
}: ProjectLocationDialogProps) {
  const model = useProjectLocation(projectId, onApplied);
  const { draft, alignment, busy, pairs, activePair, screen } = model;
  const [query, setQuery] = useState('');
  const [guard, setGuard] = useState(false);
  const pending = useRef<() => void>(() => {});
  const [cadVisible, setCadVisible] = useState(true),
    [osmVisible, setOsmVisible] = useState(true);
  const [street, setStreet] = useState(''),
    [section, setSection] = useState<LocationBounds | null>(null);
  const [longitude, setLongitude] = useState(''),
    [latitude, setLatitude] = useState('');
  const locking =
    busy === 'applying' || (screen === 'manual' && busy === 'aligning');
  const small = [
    'loading',
    'place',
    'resolving',
    'aligning',
    'error',
    'center',
  ].includes(screen);
  const address = locationLabel(model.suggestion);
  useEffect(() => {
    setQuery(locationLabel(model.suggestion));
  }, [model.suggestion]);
  useEffect(() => {
    if (!draft) return;
    setStreet(
      draft.selected_street_id ||
        (draft.street_candidates.length === 1
          ? draft.street_candidates[0].id
          : ''),
    );
    setSection(model.lastRequest?.section_bbox ?? null);
  }, [draft, model.lastRequest?.section_bbox]);
  const protect = (action: () => void) => {
    if (locking) return;
    if (model.hasUnsavedChanges) {
      pending.current = action;
      setGuard(true);
    } else action();
  };
  const cancelGuard = () => {
    setGuard(false);
    if (screen === 'place' && model.hasUnsavedChanges) {
      setQuery(address);
      model.startManual();
    }
  };
  const choose = (suggestion: LocationSuggestion) =>
    protect(() => {
      setQuery(locationLabel(suggestion));
      setSection(null);
      setStreet('');
      void model.resolve({ suggestion });
    });
  const back = () => {
    if (screen === 'manual' && model.hasReview)
      protect(() => model.discardManual());
    else model.showSearch();
  };
  const retry = () =>
    protect(() => {
      if (model.lastRequest) void model.resolve(model.lastRequest);
      else void model.load();
    });
  const cad = useMemo(() => collectionLines(draft?.cad_geometry), [draft]);
  const osm = useMemo(
    () =>
      collectionLines(draft?.map_buildings).map((line) =>
        line.map(projectMapPoint),
      ),
    [draft],
  );
  const streets = useMemo(
    () =>
      (draft?.street_candidates ?? [])
        .filter((item) => item.id === draft?.selected_street_id)
        .flatMap((item) => geometryLines(item.geometry))
        .map((line) => line.map(projectMapPoint)),
    [draft],
  );
  const mapBounds = useMemo(
    () =>
      boundsOf(
        geometryLines(draft?.search_geometry).map((line) =>
          line.map(projectMapPoint),
        ),
      ) ?? undefined,
    [draft],
  );
  const overlay = useMemo(
    () => collectionLines(alignment?.preview_buildings),
    [alignment],
  );
  const extent = alignment?.transform?.valid_extent_cad_xy;
  const boundary: LocationPoint[][] = extent
    ? [
        [
          [extent[0], extent[1]],
          [extent[2], extent[1]],
          [extent[2], extent[3]],
          [extent[0], extent[3]],
          [extent[0], extent[1]],
        ],
      ]
    : [];
  const omitted = Number(
    (
      alignment?.preview_buildings as
        { omitted_outside_extent_count?: number } | undefined
    )?.omitted_outside_extent_count ?? 0,
  );
  const displayPairs = useMemo(
    () =>
      pairs.map((pair) => ({
        cad: pair.cad_xy,
        map: pair.lon_lat ? projectMapPoint(pair.lon_lat) : undefined,
        holdout: pair.usage === 'holdout',
      })),
    [pairs],
  );
  const complete = pairs.filter((pair) => pair.cad_xy && pair.lon_lat).length;
  const title =
    screen === 'manual'
      ? 'Корректировка'
      : screen === 'street' || screen === 'center'
        ? 'Участок улицы'
        : screen === 'review'
          ? 'Совмещение'
          : 'Местоположение';
  const centerX = Number(longitude.replace(',', '.')),
    centerY = Number(latitude.replace(',', '.'));
  const validCenter =
    longitude.trim() &&
    latitude.trim() &&
    Number.isFinite(centerX) &&
    Number.isFinite(centerY) &&
    Math.abs(centerX) <= 180 &&
    Math.abs(centerY) <= 85;
  return (
    <>
      <Dialog
        open
        title={title}
        size={small ? 'form' : 'wide'}
        stableHeight={!small}
        bodyInset="none"
        onClose={() => protect(onClose)}
        closeDisabled={locking}
        header={
          <header
            data-slot="dialog-header"
            className="flex min-h-14 shrink-0 items-center justify-between gap-4 border-b border-neutral-200 px-4 py-2"
          >
            <div className="flex min-w-0 items-center gap-3">
              {screen !== 'place' && screen !== 'loading' && (
                <IconButton
                  icon={ArrowLeft}
                  label={
                    screen === 'manual' && model.hasReview
                      ? 'К наложению'
                      : 'Изменить адрес'
                  }
                  variant="ghost"
                  controlSize="compact"
                  disabled={locking}
                  onClick={back}
                />
              )}
              <div className="min-w-0">
                <div className="text-base font-semibold">{title}</div>
                {!small && (
                  <div
                    className="mt-0.5 truncate text-xs text-neutral-600"
                    title={model.suggestion?.label}
                  >
                    {address}
                  </div>
                )}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-3">
              <span
                className="text-xs text-neutral-500 tabular-nums"
                aria-label={
                  screen === 'manual'
                    ? `Пара ${activePair + 1} из 4`
                    : `Шаг ${small ? 1 : 2} из 2`
                }
              >
                {screen === 'manual'
                  ? activePair === 3
                    ? 'Контрольная пара'
                    : `Пара ${activePair + 1} из 4`
                  : `${small ? 1 : 2} из 2`}
              </span>
              <IconButton
                icon={X}
                label="Закрыть"
                variant="ghost"
                controlSize="compact"
                disabled={locking}
                onClick={() => protect(onClose)}
              />
            </div>
          </header>
        }
        footer={
          screen === 'review' && draft ? (
            <div className="flex min-h-9 items-center justify-between gap-4">
              <div className="flex min-w-0 items-center gap-4">
                <LocationDetails
                  draft={draft}
                  alignment={alignment}
                  disabled={locking}
                />
                {omitted > 0 && (
                  <span
                    className="flex items-center gap-2 text-xs text-neutral-600"
                    aria-label={`${omitted} зданий вне привязки`}
                  >
                    <TriangleAlert className="size-4 text-amber-700" />
                    Вне привязки <span className="tabular-nums">{omitted}</span>
                  </span>
                )}
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Button
                  variant="ghost"
                  disabled={locking}
                  onClick={model.startManual}
                >
                  Скорректировать
                </Button>
                {model.stale ? (
                  <Button variant="primary" onClick={retry}>
                    Обновить совмещение
                  </Button>
                ) : (
                  <Button
                    variant="primary"
                    loading={busy === 'applying'}
                    disabled={
                      Boolean(busy) ||
                      alignment?.status !== 'candidate' ||
                      Boolean(model.error)
                    }
                    onClick={async () => {
                      if (await model.apply()) onClose();
                    }}
                  >
                    Применить
                  </Button>
                )}
              </div>
            </div>
          ) : screen === 'manual' ? (
            <div className="flex min-h-9 items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <PairPicker
                  pairs={displayPairs}
                  active={activePair}
                  onSelect={model.setActivePair}
                />
                <IconButton
                  icon={Trash2}
                  label="Удалить пару"
                  variant="ghost"
                  disabled={
                    locking ||
                    (!pairs[activePair].cad_xy && !pairs[activePair].lon_lat)
                  }
                  onClick={model.clearPair}
                />
                <span className="text-xs text-neutral-600 tabular-nums">
                  Готово {complete} / 4
                </span>
              </div>
              <Button
                variant="primary"
                loading={locking}
                endIcon={model.canFit ? undefined : <ArrowRight />}
                disabled={
                  locking ||
                  !pairs[activePair].cad_xy ||
                  !pairs[activePair].lon_lat
                }
                onClick={() =>
                  model.canFit ? void model.align(true) : model.nextPair()
                }
              >
                {model.canFit ? 'Проверить совмещение' : 'Следующая пара'}
              </Button>
            </div>
          ) : screen === 'street' && model.suggestion ? (
            <div className="flex min-h-9 justify-end">
              <Button
                variant="primary"
                disabled={!street || !section || Boolean(busy)}
                onClick={() =>
                  void model.resolve({
                    ...model.lastRequest,
                    suggestion: model.suggestion!,
                    street_candidate_id: street,
                    section_bbox: section,
                  })
                }
              >
                Загрузить участок
              </Button>
            </div>
          ) : screen === 'center' && model.suggestion ? (
            <div className="flex min-h-9 justify-end">
              <Button
                variant="primary"
                disabled={!validCenter || Boolean(busy)}
                onClick={() =>
                  void model.resolve({
                    ...model.lastRequest,
                    suggestion: model.suggestion!,
                    center_lon_lat: [centerX, centerY],
                  })
                }
              >
                Загрузить участок
              </Button>
            </div>
          ) : undefined
        }
      >
        {screen === 'place' && (
          <div className="p-4">
            <LocationSearchField
              projectId={projectId}
              value={query}
              onChange={setQuery}
              onSelect={choose}
              disabled={Boolean(busy)}
              configurationRevision={model.serviceConfigurationRevision}
            />
            {model.state && !model.state.configured && (
              <div role="status" className="mt-3 text-xs text-neutral-600">
                {model.state.message?.replace(/[.!]$/, '') ||
                  'Поиск адресов не настроен'}
              </div>
            )}
            {model.hasUnsavedChanges && (
              <Button className="mt-4" onClick={model.startManual}>
                Продолжить корректировку
              </Button>
            )}
            <LocationServiceSettingsForm
              onChanged={model.updateServiceSettings}
            />
          </div>
        )}
        {['loading', 'resolving', 'aligning'].includes(screen) && (
          <div
            role="status"
            aria-live="polite"
            className="flex flex-col items-start gap-3 p-4"
          >
            {address && (
              <>
                <MapPin className="size-6 text-neutral-500" />
                <span>{address}</span>
              </>
            )}
            <div className="flex items-center gap-2 text-neutral-600">
              <LoaderCircle className="size-4 animate-spin motion-reduce:animate-none" />
              {screen === 'loading'
                ? 'Местоположение'
                : screen === 'resolving'
                  ? 'Здания OpenStreetMap'
                  : 'Совмещение контуров'}
            </div>
          </div>
        )}
        {screen === 'center' && (
          <div className="grid gap-4 p-4">
            <div className="text-xs text-neutral-600">
              Уточните центр участка
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Долгота">
                <TextInput
                  inputMode="decimal"
                  value={longitude}
                  onChange={(event) => setLongitude(event.target.value)}
                />
              </Field>
              <Field label="Широта">
                <TextInput
                  inputMode="decimal"
                  value={latitude}
                  onChange={(event) => setLatitude(event.target.value)}
                />
              </Field>
            </div>
          </div>
        )}
        {screen === 'error' && (
          <div role="alert" className="flex flex-col items-start gap-3 p-4">
            <TriangleAlert className="size-6 text-amber-700" />
            <div className="font-medium">Не удалось загрузить участок</div>
            <div className="text-neutral-600">{model.error}</div>
            <Button onClick={retry}>Повторить</Button>
          </div>
        )}
        {screen === 'street' && draft && (
          <LocationStreetWorkspace
            draft={draft}
            selected={street}
            section={section}
            onSelect={(id, bounds) => {
              setStreet(id);
              setSection(bounds);
            }}
          />
        )}
        {screen === 'review' && draft && (
          <div className="relative flex min-h-0 flex-1">
            <LocationPlan
              key={`review-${draft.id}`}
              title="Совмещение CAD и OSM"
              fitBounds={draft.cad_bounds ?? undefined}
              lines={cadVisible ? cad : []}
              overlay={osmVisible ? overlay : []}
              boundary={boundary}
            />
            <div
              className="rounded-control absolute top-4 left-4 flex flex-wrap gap-1 border border-neutral-300 bg-white p-1"
              role="group"
              aria-label="Видимость контуров"
            >
              {(['CAD', 'OSM'] as const).map((label, index) => (
                <Button
                  key={label}
                  variant="ghost"
                  controlSize="compact"
                  aria-pressed={index === 0 ? cadVisible : osmVisible}
                  className="gap-2 aria-[pressed=false]:opacity-45"
                  onClick={() =>
                    index === 0
                      ? setCadVisible(!cadVisible)
                      : setOsmVisible(!osmVisible)
                  }
                  startIcon={
                    <svg viewBox="0 0 16 16" aria-hidden="true">
                      <path
                        d="M1 8h14"
                        fill="none"
                        stroke={
                          index === 0
                            ? 'var(--color-neutral-500)'
                            : 'var(--color-blue-600)'
                        }
                        strokeWidth="1.5"
                        strokeDasharray={index === 1 ? '4 2' : undefined}
                      />
                    </svg>
                  }
                >
                  {label}
                </Button>
              ))}
            </div>
            <a
              href="https://www.openstreetmap.org/copyright"
              target="_blank"
              rel="noreferrer"
              className="absolute right-4 bottom-2 bg-white/90 px-1 text-xs text-neutral-600 underline"
            >
              © OpenStreetMap contributors
            </a>
          </div>
        )}
        {screen === 'manual' && draft && (
          <LocationManualWorkspace
            key={`manual-${draft.id}`}
            pairs={displayPairs}
            active={activePair}
            cad={cad}
            osm={osm}
            streets={streets}
            cadBounds={draft.cad_bounds ?? undefined}
            mapBounds={mapBounds}
            busy={locking}
            onPlace={(side, point) =>
              model.placePoint(
                side === 'cad' ? 'cad_xy' : 'lon_lat',
                side === 'cad' ? point : unprojectMapPoint(point),
              )
            }
            onEdit={(side) =>
              model.editPoint(side === 'cad' ? 'cad_xy' : 'lon_lat')
            }
          />
        )}
        {(screen === 'manual' || screen === 'review') && model.error && (
          <div
            role="alert"
            className="flex shrink-0 items-center gap-3 border-t border-neutral-200 bg-amber-50 px-4 py-2 text-xs text-amber-800"
          >
            {model.error}
          </div>
        )}
      </Dialog>
      <Dialog
        open={guard}
        title="Отменить корректировку?"
        onClose={cancelGuard}
        footer={
          <div className="flex justify-end gap-2">
            <Button onClick={cancelGuard}>Продолжить работу</Button>
            <Button
              variant="primary"
              onClick={() => {
                setGuard(false);
                model.discardManual();
                pending.current();
              }}
            >
              Отменить корректировку
            </Button>
          </div>
        }
      >
        <dl className="flex items-center justify-between gap-4">
          <dt className="text-neutral-600">Несохранённые пары</dt>
          <dd className="m-0 tabular-nums">
            {pairs.filter((pair) => pair.cad_xy || pair.lon_lat).length}
          </dd>
        </dl>
      </Dialog>
    </>
  );
}
