import { useState } from 'react';
import { Button, Popover, PopoverTrigger, PopoverPopup } from '@green/ui';
import type {
  LocationDraft,
  LocationBounds,
  LocationPoint,
} from '@green/api-client';
import {
  boundsOf,
  geometryLines,
  projectMapPoint,
  unprojectMapPoint,
  sectionAround,
} from '@/features/project-location/model/locationGeometry';
import { LocationPlan } from './LocationPlan';

export function LocationStreetWorkspace({
  draft,
  selected,
  section,
  onSelect,
}: {
  draft: LocationDraft;
  selected: string;
  section: LocationBounds | null;
  onSelect: (id: string, section: LocationBounds | null) => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const candidates = draft.street_candidates.map((candidate) => ({
    ...candidate,
    lines: geometryLines(candidate.geometry).map((line) =>
      line.map(projectMapPoint),
    ),
  }));
  const boundary: LocationPoint[][] = section
    ? [
        [
          projectMapPoint([section[0], section[1]]),
          projectMapPoint([section[2], section[1]]),
          projectMapPoint([section[2], section[3]]),
          projectMapPoint([section[0], section[3]]),
          projectMapPoint([section[0], section[1]]),
        ],
      ]
    : [];
  function pick(point: LocationPoint) {
    let best = Infinity,
      id = '';
    for (const candidate of candidates.filter(
      (candidate) => !selected || candidate.id === selected,
    ))
      for (const line of candidate.lines)
        for (let i = 1; i < line.length; i++) {
          const a = line[i - 1],
            b = line[i],
            dx = b[0] - a[0],
            dy = b[1] - a[1];
          const t = Math.max(
            0,
            Math.min(
              1,
              ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) /
                (dx * dx + dy * dy || 1),
            ),
          );
          const distance = Math.hypot(
            point[0] - a[0] - t * dx,
            point[1] - a[1] - t * dy,
          );
          if (distance < best) {
            best = distance;
            id = candidate.id;
          }
        }
    if (id && best < 250)
      onSelect(id, sectionAround(unprojectMapPoint(point), 350));
  }
  return (
    <div className="relative flex min-h-0 flex-1">
      <LocationPlan
        key={selected}
        title="Участки улицы OSM"
        fitBounds={
          selected
            ? (boundsOf(
                candidates.find((candidate) => candidate.id === selected)
                  ?.lines ?? [],
              ) ?? undefined)
            : undefined
        }
        lines={[]}
        streets={candidates.flatMap((c) => c.lines)}
        overlay={candidates.find((c) => c.id === selected)?.lines ?? []}
        boundary={boundary}
        onPoint={pick}
      />
      <div className="absolute top-4 left-4">
        <Popover open={menuOpen} onOpenChange={setMenuOpen}>
          <PopoverTrigger
            render={
              <Button
                variant="ghost"
                className="border border-neutral-300 bg-white"
              />
            }
          >
            {selected
              ? `Участок ${candidates.findIndex((c) => c.id === selected) + 1}`
              : 'Участок улицы'}
          </PopoverTrigger>
          <PopoverPopup
            align="start"
            className="max-h-64 w-64 overflow-y-auto p-0"
          >
            {candidates.map((candidate, index) => (
              <Button
                key={candidate.id}
                variant="ghost"
                className="w-full justify-start rounded-none px-3"
                aria-pressed={candidate.id === selected}
                onClick={() => {
                  onSelect(candidate.id, null);
                  setMenuOpen(false);
                }}
              >
                Участок {index + 1}
              </Button>
            ))}
          </PopoverPopup>
        </Popover>
      </div>
      {!section && (
        <div className="rounded-control pointer-events-none absolute bottom-3 left-4 border border-neutral-300 bg-white px-3 py-2 text-xs text-neutral-600">
          Укажите участок на карте
        </div>
      )}
      <a
        href="https://www.openstreetmap.org/copyright"
        target="_blank"
        rel="noreferrer"
        className="absolute right-4 bottom-2 bg-white/90 px-1 text-xs text-neutral-600 underline"
      >
        © OpenStreetMap contributors
      </a>
    </div>
  );
}
