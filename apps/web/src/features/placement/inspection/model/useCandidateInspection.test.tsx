import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, afterEach } from 'vitest';
import {
  api,
  type Project,
  type PatternPreview,
  type ChangeSetPreview,
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
