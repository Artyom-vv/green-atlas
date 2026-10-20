import { QueryClient } from '@tanstack/react-query';
import { api, ApiClientError } from '@green/api-client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { projectFixture } from '@/test/projectFixture';
import { workspaceSceneQuery } from './workspaceQueries';

type SceneView = Awaited<ReturnType<typeof api.getSceneView>>;

const transport = vi.hoisted(() => ({ read: vi.fn() }));
vi.mock('@green/api-client', async (load) => {
  const original = await load<typeof import('@green/api-client')>();
  return {
    ...original,
    api: { ...original.api, getSceneView: transport.read },
  };
});

const clients: QueryClient[] = [];
afterEach(() => {
  clients.forEach((client) => client.clear());
  clients.length = 0;
  transport.read.mockReset();
});

function query() {
  const client = new QueryClient({
    defaultOptions: { queries: { retryDelay: 0 } },
  });
  clients.push(client);
  const project = projectFixture();
  return {
    client,
    options: workspaceSceneQuery(project.id!, project, 0, true),
  };
}

describe('workspace scene reads', () => {
  it('coalesces simultaneous reads of the same version and returns its cached result', async () => {
    let finish!: (view: SceneView) => void;
    transport.read.mockReturnValue(
      new Promise<SceneView>((resolve) => {
        finish = resolve;
      }),
    );
    const { client, options } = query();
    const first = client.fetchQuery(options);
    const second = client.fetchQuery(options);
    expect(transport.read).toHaveBeenCalledOnce();
    const view = {
      scene: { plan_version: 1 },
      environment: { id: 'prepared' },
    } as unknown as SceneView;
    finish(view);
    const expected = { ...view.scene, displayEnvironment: view.environment };
    await expect(first).resolves.toEqual(expected);
    await expect(second).resolves.toEqual(expected);
    await expect(client.fetchQuery(options)).resolves.toEqual(expected);
    expect(transport.read).toHaveBeenCalledOnce();
  });

  it.each([409, 422])(
    'does not retry a refused HTTP %s response',
    async (status) => {
      const error = new ApiClientError(
        'SCENE_RENDER_UNAVAILABLE',
        'Refused',
        {},
        {},
        status,
      );
      transport.read.mockRejectedValue(error);
      const { client, options } = query();
      await expect(client.fetchQuery(options)).rejects.toBe(error);
      expect(transport.read).toHaveBeenCalledOnce();
    },
  );

  it('retries a server failure once and accepts the recovered scene', async () => {
    const view = {
      scene: { plan_version: 2 },
      environment: undefined,
    } as unknown as SceneView;
    transport.read
      .mockRejectedValueOnce(
        new ApiClientError('HTTP_ERROR', 'Bad gateway', {}, {}, 502),
      )
      .mockResolvedValueOnce(view);
    const { client, options } = query();
    await expect(client.fetchQuery(options)).resolves.toEqual({
      ...view.scene,
      displayEnvironment: undefined,
    });
    expect(transport.read).toHaveBeenCalledTimes(2);
  });
});
