import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../index';

const projectPayload = (id: string, version: number) => ({
  id,
  name: 'Конкурентный проект',
  status: 'empty',
  layers: [],
  geometry_version: 0,
  state_version: version,
  created_at: '2026-08-12T10:00:00Z',
  updated_at: '2026-08-12T10:00:00Z',
});

describe('API project concurrency headers', () => {
  afterEach(() => vi.unstubAllGlobals());
  it('pins deletion to its captured version while keeping the existing POST payload', async () => {
    const projectId = 'captured-delete';
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(projectPayload(projectId, 12))),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ version: 5, objects: [] })),
      );
    vi.stubGlobal('fetch', fetchMock);
    await api.getProject(projectId, false);
    await api.deletePlanObjects(projectId, ['tree-1'], {
      expectedStateVersion: 10,
    });
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toContain(`/api/projects/${projectId}/plan/objects/delete`);
    expect(init.method).toBe('POST');
    expect(new Headers(init.headers).get('If-Match')).toBe('"10"');
    expect(JSON.parse(init.body)).toEqual({ ids: ['tree-1'] });
  });
  it.each(['zones', 'manual-plan'] as const)(
    'pins %s startup writes to the captured project version',
    async (kind) => {
      const projectId = `startup-${kind}`;
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(
          new Response(JSON.stringify(projectPayload(projectId, 12))),
        )
        .mockResolvedValueOnce(
          new Response(JSON.stringify(projectPayload(projectId, 13))),
        );
      vi.stubGlobal('fetch', fetchMock);
      const options = Object.freeze({ expectedStateVersion: 11 });
      await api.getProject(projectId, false);
      if (kind === 'zones') await api.savePlantingZones(projectId, [], options);
      else await api.createManualPlan(projectId, options);
      const init = fetchMock.mock.calls[1]?.[1];
      expect(new Headers(init?.headers).get('If-Match')).toBe('"11"');
      expect(new Headers(init?.headers).get('Content-Type')).toBe(
        'application/json',
      );
      expect(init?.method).toBe(kind === 'zones' ? 'PUT' : 'POST');
      expect(init?.body).toBe(
        kind === 'zones' ? JSON.stringify({ zones: [] }) : undefined,
      );
      expect(options.expectedStateVersion).toBe(11);
    },
  );
  it('preserves the explicit single-placement snapshot after a later project read', async () => {
    const projectId = 'frozen-single-placement';
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(projectPayload(projectId, 10)), {
          headers: { 'X-Project-State-Version': '10' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({}), {
          headers: { 'X-Project-State-Version': '11' },
        }),
      );
    vi.stubGlobal('fetch', fetchMock);
    await api.getProject(projectId, false);
    await api.addPlanObject(
      projectId,
      {
        kind: 'tree',
        x: 20,
        y: 20,
        species_revision_id: 'oak',
        size_class: 'standard',
      },
      { expectedStateVersion: 9 },
    );
    expect(
      new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get('If-Match'),
    ).toBe('"9"');
    expect(JSON.parse(fetchMock.mock.calls[1]?.[1]?.body)).toEqual({
      kind: 'tree',
      x: 20,
      y: 20,
      species_revision_id: 'oak',
      size_class: 'standard',
    });
  });
  it('does not turn building screening into a write or adopt a concurrent project revision', async () => {
    const projectId = 'building-screen-read-only';
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(projectPayload(projectId, 6)), {
          headers: { 'X-Project-State-Version': '6' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({}), {
          headers: { 'X-Project-State-Version': '7' },
        }),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify([])));
    vi.stubGlobal('fetch', fetchMock);
    await api.getProject(projectId, false);
    await api.previewBuildingScreen(projectId, {
      base_plan_version: 1,
      zone_ids: ['west'],
      screen_side: 'perimeter',
    });
    await api.createExport(projectId);
    expect(
      new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get('If-Match'),
    ).toBeNull();
    expect(
      new Headers(fetchMock.mock.calls[2]?.[1]?.headers).get('If-Match'),
    ).toBe('"6"');
  });

  it('sends the last project version and advances it after a successful mutation', async () => {
    const projectId = 'if-match-project';
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(projectPayload(projectId, 7)), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'X-Project-State-Version': '7',
          },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify([]), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'X-Project-State-Version': '8',
          },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ id: 'export-1' }), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'X-Project-State-Version': '9',
          },
        }),
      );
    vi.stubGlobal('fetch', fetchMock);

    await api.getProject(projectId, false);
    await api.createExport(projectId);
    await api.createExport(projectId);

    expect(
      new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get('If-Match'),
    ).toBe('"7"');
    expect(
      new Headers(fetchMock.mock.calls[2]?.[1]?.headers).get('If-Match'),
    ).toBe('"8"');
  });

  it('does not adopt a project version from operation polling', async () => {
    const projectId = 'polling-project';
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(projectPayload(projectId, 3)), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'X-Project-State-Version': '3',
          },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            project_id: projectId,
            kind: 'calculate_geometry',
            status: 'running',
            progress: 50,
            stage: 'Расчёт',
            project_state_version: 3,
          }),
          {
            status: 200,
            headers: {
              'Content-Type': 'application/json',
              'X-Project-State-Version': '4',
            },
          },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            code: 'PROJECT_VERSION_CONFLICT',
            message: 'Проект изменён',
            field_errors: {},
            details: { expected_version: 3, current_version: 4 },
          }),
          { status: 409, headers: { 'Content-Type': 'application/json' } },
        ),
      );
    vi.stubGlobal('fetch', fetchMock);

    await api.getProject(projectId, false);
    await api.getOperation(projectId, 'operation-1');
    await expect(api.createExport(projectId)).rejects.toBeDefined();

    expect(
      new Headers(fetchMock.mock.calls[2]?.[1]?.headers).get('If-Match'),
    ).toBe('"3"');
  });

  it('treats a placement preview as read-only even though it uses POST', async () => {
    const projectId = 'preview-project';
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(projectPayload(projectId, 6)), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'X-Project-State-Version': '6',
          },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            allowed: true,
            status: 'allowed',
            kind: 'tree',
            x: 20,
            y: 20,
            radius: 1.6,
            reason: 'Позиция проходит текущую проверку',
          }),
          {
            status: 200,
            headers: {
              'Content-Type': 'application/json',
              'X-Project-State-Version': '6',
            },
          },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify([]), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'X-Project-State-Version': '7',
          },
        }),
      );
    vi.stubGlobal('fetch', fetchMock);

    await api.getProject(projectId, false);
    await api.checkPlacement(projectId, { kind: 'tree', x: 20, y: 20 });
    await api.createExport(projectId);

    expect(
      new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get('If-Match'),
    ).toBeNull();
    expect(
      new Headers(fetchMock.mock.calls[2]?.[1]?.headers).get('If-Match'),
    ).toBe('"6"');
  });

  it('advances the version after a state mutation that returns a domain projection', async () => {
    const projectId = 'projection-project';
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(projectPayload(projectId, 11)), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'X-Project-State-Version': '11',
          },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify([]), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'X-Project-State-Version': '12',
          },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ id: 'export-2' }), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'X-Project-State-Version': '13',
          },
        }),
      );
    vi.stubGlobal('fetch', fetchMock);

    await api.getProject(projectId, false);
    await api.createExport(projectId);
    await api.createExport(projectId);

    expect(
      new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get('If-Match'),
    ).toBe('"11"');
    expect(
      new Headers(fetchMock.mock.calls[2]?.[1]?.headers).get('If-Match'),
    ).toBe('"12"');
  });
});
