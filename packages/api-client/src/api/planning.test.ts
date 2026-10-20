import { afterEach, expect, expectTypeOf, it, vi } from 'vitest';
import type { ChangeSetPreview, PlanChangeSetDraft } from '../contracts';
import { planningApi } from './planning';

afterEach(() => vi.unstubAllGlobals());
it.each(['undoPlanChange', 'redoPlanChange'] as const)(
  'pins %s to the captured history revision',
  async (action) => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'));
    vi.stubGlobal('fetch', fetch);
    await planningApi[action]('saved', { expectedStateVersion: 17 });
    const [, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(new Headers(init.headers).get('If-Match')).toBe('"17"');
  },
);
it('pins a preview to its captured revision while preserving the abort signal', async () => {
  const fetch = vi
    .fn()
    .mockResolvedValue(new Response(JSON.stringify({ id: 'preview' })));
  vi.stubGlobal('fetch', fetch);
  const controller = new AbortController();
  await planningApi.previewPlanChanges(
    'saved',
    {} as PlanChangeSetDraft,
    controller.signal,
    { expectedStateVersion: 17 },
  );
  const [, init] = fetch.mock.calls[0] as [string, RequestInit];
  expect(new Headers(init.headers).get('If-Match')).toBe('"17"');
  expect(init.method).toBe('POST');
  expect(init.signal?.aborted).toBe(false);
});

it('reads an exact mutation receipt using GET and the captured source revision', async () => {
  const fetch = vi
    .fn()
    .mockResolvedValue(
      new Response(JSON.stringify({ status: 'not_found', receipt: null })),
    );
  vi.stubGlobal('fetch', fetch);
  await planningApi.getPlanChangeReceipt(
    'saved',
    {
      id: 'preview',
      digest: 'a'.repeat(64),
      base_plan_version: 4,
    } as ChangeSetPreview,
    9,
  );
  const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
  const address = new URL(url);
  expect(address.pathname).toBe(
    '/api/projects/saved/plan/change-sets/preview/receipt',
  );
  expect(address.searchParams.get('base_state_version')).toBe('9');
  expect(address.searchParams.get('base_plan_version')).toBe('4');
  expect(address.searchParams.get('digest')).toBe('a'.repeat(64));
  expect(init.method ?? 'GET').toBe('GET');
  expect(init.body).toBeUndefined();
  expect(init.cache).toBe('no-store');
});

it('повторяет zone_ids и кодирует специальные символы пути и параметров ровно один раз', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response('{}'));
  vi.stubGlobal('fetch', fetch);
  const projectId = 'проект /#%?';
  const zoneIds = ['запад/1 #?%', 'восток &2'];

  await planningApi.getBuildingScreenTargets(projectId, zoneIds);

  const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
  const address = new URL(url);
  expect(address.pathname).toBe(
    `/api/projects/${encodeURIComponent(projectId)}/building-screen/targets`,
  );
  expect(address.searchParams.getAll('zone_ids')).toEqual(zoneIds);
  expect([...address.searchParams.keys()]).toEqual(['zone_ids', 'zone_ids']);
  expect(address.hash).toBe('');
  expect(init.method).toBe('GET');
});

it('читает ассортимент без аргументов и не переносит контекст запроса в URL', async () => {
  expectTypeOf<Parameters<typeof planningApi.getAssortment>>().toEqualTypeOf<
    []
  >();
  const fetch = vi.fn().mockResolvedValue(new Response('{}'));
  vi.stubGlobal('fetch', fetch);
  const queryFn: (context: {
    queryKey: readonly string[];
    signal: AbortSignal;
  }) => ReturnType<typeof planningApi.getAssortment> =
    planningApi.getAssortment;

  await queryFn({
    queryKey: ['assortment'],
    signal: new AbortController().signal,
  });

  const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
  const address = new URL(url);
  expect(address.pathname).toBe('/api/species/assortment');
  expect(address.search).toBe('');
  expect(init.method).toBe('GET');
  expect(init.body).toBeUndefined();
});
