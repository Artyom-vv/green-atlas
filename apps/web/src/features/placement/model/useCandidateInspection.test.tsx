import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, afterEach } from 'vitest';
import {
  api,
  type Project,
  type PatternPreview,
  type ChangeSetPreview,
  type PatternPreviewRequest,
  type PlacementCheck,
} from '@green/api-client';
import { useCandidateInspection } from './useCandidateInspection';

afterEach(() => vi.restoreAllMocks());
const candidate = {
  x: 10,
  y: 20,
  code: 'NATIVE_LOCAL_UNKNOWN',
  category: 'data' as const,
  status: 'unknown' as const,
  reason: 'Принадлежность территории не подтверждена',
  candidate: {
    kind: 'tree' as const,
    x: 10,
    y: 20,
    species_revision_id: 'rowan',
    size_class: 'standard' as const,
    spacing_policy: 'balanced' as const,
    locked: false,
  },
};
const project = {
  id: 'project',
  state_version: 4,
  geometry_version: 3,
  plan: { version: 2 },
} as Project;
const preview = {
  pattern_id: 'pattern',
  type: 'fill',
  requested_count: 2,
  accepted_count: 1,
  skipped: [candidate],
  change_set: { additions: [{ kind: 'tree', x: 30, y: 40 }] },
} as PatternPreview;
const reply = (status = 'allowed') =>
  ({
    id: 'trial',
    can_apply: status === 'allowed',
    additions: [],
    candidate_results: [{ status, zone_id: 'selected', reason: 'Проверено' }],
  }) as unknown as ChangeSetPreview;
const options = () => ({
  project,
  preview,
  zoneIds: ['selected'],
  onFocus: vi.fn(),
  onApply: vi.fn(),
});

describe('candidate inspection', () => {
  const inspectionRequest = { type: 'fill', plant_kind: 'tree', species_revision_id: 'rowan', size_class: 'standard' } as PatternPreviewRequest;
  const inspected = { kind: 'tree', x: 12, y: 21, status: 'allowed', allowed: true, zone_id: 'selected',
    geometry_evidence: { state: 'excluded', causes: [{ code: 'clearance' }] } } as PlacementCheck;

  it('can inspect an arbitrary point even with zero generated candidates and never apply it', async () => {
    const check = vi.spyOn(api, 'checkPlacement').mockResolvedValue(inspected);
    const changes = vi.spyOn(api, 'previewPlanChanges');
    const props = { ...options(), request: inspectionRequest, preview: { ...preview, accepted_count: 0, skipped: [] } };
    const { result } = renderHook(() => useCandidateInspection(props));
    act(() => result.current.startExplaining());
    expect(result.current.picking).toBe(true);
    await act(() => result.current.probe([12, 21]));
    expect(check.mock.calls[0][1]).toMatchObject({ explain_geometry: true, x: 12, y: 21, species_revision_id: 'rowan',
      base_plan_version: 2, state_version: 4, geometry_version: 3 });
    expect(result.current.marker?.status).toBe('blocked');
    expect(result.current.diagnosis).toEqual(inspected);
    act(() => result.current.apply());
    expect(props.onApply).not.toHaveBeenCalled();
    expect(changes).not.toHaveBeenCalled();
  });

  it('discards a late diagnostic answer after reset', async () => {
    let resolve!: (reply: PlacementCheck) => void;
    vi.spyOn(api, 'checkPlacement').mockImplementation(() => new Promise((r) => { resolve = r; }));
    const { result } = renderHook(() => useCandidateInspection({ ...options(), request: inspectionRequest }));
    act(() => result.current.startExplaining());
    let pending!: Promise<void>;
    act(() => { pending = result.current.probe([12, 21]); });
    act(() => result.current.reset());
    await act(async () => { resolve(inspected); await pending; });
    expect(result.current.marker).toBeUndefined();
    expect(result.current.diagnosis).toBeUndefined();
  });

  it('focuses without writing, then checks the correction with proposed neighbours', async () => {
    const request = vi
      .spyOn(api, 'previewPlanChanges')
      .mockResolvedValue(reply());
    const props = options();
    const { result } = renderHook(() => useCandidateInspection(props));
    act(() => result.current.select(candidate, 0));
    expect(props.onFocus).toHaveBeenCalledWith([10, 20]);
    expect(request).not.toHaveBeenCalled();
    await act(() => result.current.probe([12, 21]));
    expect(request.mock.calls[0][1]).toMatchObject({
      source: 'pattern',
      base_plan_version: 2,
      operations: [
        { type: 'add', object: { x: 30, y: 40 } },
        { type: 'add', object: { x: 12, y: 21, species_revision_id: 'rowan' } },
      ],
    });
    expect(props.onApply).not.toHaveBeenCalled();
    act(() => result.current.apply());
    expect(props.onApply).toHaveBeenCalledWith(reply());
  });

  it('does not permit a local unknown or another working zone', async () => {
    const request = vi
      .spyOn(api, 'previewPlanChanges')
      .mockResolvedValue(reply('unknown'));
    const props = options();
    const { result } = renderHook(() => useCandidateInspection(props));
    act(() => result.current.select(candidate, 0));
    await act(() => result.current.probe([12, 21]));
    act(() => result.current.apply());
    expect(props.onApply).not.toHaveBeenCalled();
    request.mockResolvedValue({
      ...reply(),
      candidate_results: [
        { ...reply().candidate_results![0], zone_id: 'other' },
      ],
    });
    await act(() => result.current.probe([13, 22]));
    act(() => result.current.apply());
    expect(props.onApply).not.toHaveBeenCalled();
    expect(result.current.error).toBe('Позиция вне выбранных рабочих участков');
  });

  it('ignores a late answer after a new selection', async () => {
    let resolve!: (value: ChangeSetPreview) => void;
    vi.spyOn(api, 'previewPlanChanges').mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    const props = options();
    const { result } = renderHook(() => useCandidateInspection(props));
    act(() => result.current.select(candidate, 0));
    let pending!: Promise<void>;
    act(() => {
      pending = result.current.probe([12, 21]);
    });
    act(() => result.current.select({ ...candidate, x: 99 }, 1));
    await act(async () => {
      resolve(reply());
      await pending;
    });
    await waitFor(() =>
      expect(result.current.marker?.coordinate).toEqual([99, 20]),
    );
    expect(result.current.trial).toBeUndefined();
  });
});
