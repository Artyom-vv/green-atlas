import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  createMemoryRouter,
  RouterProvider,
  useNavigate,
} from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, type Project } from '@green/api-client';
import type { ReactNode } from 'react';
import { useProjectWorkContribution } from '@/features/project-navigation/model/ProjectWorkContext';
import { ProjectWorkProvider } from '@/features/project-navigation/model/ProjectWorkProvider';
import { useProjectWrite } from '@/entities/project/model/useProjectWrite';

vi.spyOn(api, 'getProjectRead');
const project: Awaited<ReturnType<typeof api.getProjectRead>> = {
  id: 'p',
  name: 'Проект',
  status: 'mapped',
  state_version: 2,
  geometry_version: 1,
  map_ready: false,
  resume_kind: 'source_preparation',
  prepared_state: 'missing',
  layers: [],
};
const request = (send: () => Promise<Project>) => ({
  send,
  matches: (fresh: Project) => fresh.map_ready,
});
function controller(onProject = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return {
    ...renderHook(() => useProjectWrite(project, onProject), { wrapper }),
    onProject,
  };
}
beforeEach(() => {
  vi.resetAllMocks();
  // Node Request cannot consume the data router's jsdom AbortSignal.
  const NativeRequest = Request;
  vi.stubGlobal(
    'Request',
    class extends NativeRequest {
      constructor(input: RequestInfo | URL, init?: RequestInit) {
        super(input, { ...init, signal: undefined });
      }
    },
  );
});
afterEach(() => vi.unstubAllGlobals());
describe('source metadata current-state evidence', () => {
  it('publishes a delayed acknowledgement to the leave guard before owned navigation', async () => {
    let acknowledge!: (updated: Project) => void;
    const response = new Promise<Project>((resolve) => {
      acknowledge = resolve;
    });
    const send = vi.fn(() => response);
    const onProject = vi.fn();
    function Owner() {
      const write = useProjectWrite(project, onProject);
      const navigate = useNavigate();
      useProjectWorkContribution('source-decisions', write);
      return (
        <button
          disabled={write.blocked}
          onClick={() => {
            void write.execute(request(send)).then(() => {
              navigate('/projects/p/workspace');
            });
          }}
        >
          Открыть редактор
        </button>
      );
    }
    const client = new QueryClient();
    const router = createMemoryRouter(
      [
        {
          path: '/projects/p/setup',
          element: (
            <ProjectWorkProvider projectId="p">
              <Owner />
            </ProjectWorkProvider>
          ),
        },
        { path: '/projects/p/workspace', element: <h1>Карта проекта</h1> },
      ],
      {
        initialEntries: ['/projects/p/setup'],
        future: { v7_relativeSplatPath: true },
      },
    );
    render(
      <QueryClientProvider client={client}>
        <RouterProvider router={router} future={{ v7_startTransition: true }} />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Открыть редактор' }));
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Открыть редактор' }),
      ).toBeDisabled(),
    );
    expect(send).toHaveBeenCalledOnce();
    await act(async () => {
      acknowledge({ ...project, state_version: 3, map_ready: true });
    });
    expect(
      await screen.findByRole('heading', { name: 'Карта проекта' }),
    ).toBeVisible();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(onProject).toHaveBeenCalledWith(
      expect.objectContaining({ state_version: 3, map_ready: true }),
    );
  });

  it('resolves a lost no-op response by a matching fresh value and version floor without another POST', async () => {
    const send = vi.fn().mockRejectedValue(new TypeError('response lost'));
    vi.mocked(api.getProjectRead).mockResolvedValue({
      ...project,
      map_ready: true,
    });
    const { result, onProject } = controller();
    await act(async () => {
      await result.current.execute(request(send)).catch(() => undefined);
    });
    expect(result.current.writeOutcome).toBe('unknown');
    await act(async () => {
      await result.current.recover();
    });
    expect(result.current.writeOutcome).toBe('idle');
    expect(send).toHaveBeenCalledOnce();
    expect(onProject).toHaveBeenCalledWith(
      expect.objectContaining({ map_ready: true, state_version: 2 }),
    );
  });
  it.each(['wrong value', 'older revision'] as const)(
    'keeps %s unknown and refuses to repeat the input write',
    async (kind) => {
      const send = vi.fn().mockRejectedValue(new TypeError('response lost'));
      vi.mocked(api.getProjectRead).mockResolvedValue({
        ...project,
        state_version: kind === 'older revision' ? 1 : 2,
        map_ready: kind !== 'wrong value',
      });
      const { result } = controller();
      await act(async () => {
        await result.current.execute(request(send)).catch(() => undefined);
        await result.current.recover();
      });
      await act(async () => {
        await result.current.execute(request(send)).catch(() => undefined);
      });
      expect(result.current.writeOutcome).toBe('unknown');
      expect(result.current.blocked).toBe(true);
      expect(send).toHaveBeenCalledOnce();
    },
  );
  it('retains commit proof when publication and subsequent GET fail', async () => {
    const send = vi
      .fn()
      .mockResolvedValue({ ...project, state_version: 3, map_ready: true });
    const onProject = vi.fn().mockImplementation(() => {
      throw new Error('publication failed');
    });
    vi.mocked(api.getProjectRead).mockRejectedValue(new TypeError('offline'));
    const { result } = controller(onProject);
    await act(async () => {
      await result.current.execute(request(send)).catch(() => undefined);
      await result.current.recover();
    });
    await waitFor(() => expect(result.current.writeOutcome).toBe('confirmed'));
    expect(result.current.readRecovery).toBe(true);
    expect(send).toHaveBeenCalledOnce();
  });
});
