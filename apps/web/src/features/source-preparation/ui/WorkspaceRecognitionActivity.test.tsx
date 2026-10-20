import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { LayerRecognition, Project } from '@green/api-client';
import { nativeProject } from '@/entities/source-data/model/nativeDxfFixtures';
import { preparationApi } from '../api/preparationApi';
import { recognitionKey } from '../api/layerRecognitionQuery';
import { sourceIdentity } from '../model/sourcePreparation';
import { WorkspaceRecognitionActivity } from './WorkspaceRecognitionActivity';

const source: Project = {
  ...nativeProject,
  layers: [
    {
      id: 'road',
      source_name: 'ROAD',
      suggested_kind: 'road',
      mapped_kind: 'road',
      mapping_confirmed: false,
      required: false,
      object_count: 1,
      geometry_complete: true,
      color: '#111',
      linetype: 'CONTINUOUS',
      visible: true,
    },
  ],
};
const response = (status: LayerRecognition['status']): LayerRecognition => ({
  source_sha256: source.source_file!.content_sha256!,
  provider: 'openai/gpt-6-luna',
  categories: [],
  proposals: [],
  status,
  stage_processed_count: 0,
  stage_total_count: 0,
  processed_count: 1,
  total_count: 1,
});
const clients: QueryClient[] = [];
beforeEach(() => {
  vi.spyOn(preparationApi, 'subscribeLayerRecognition').mockReturnValue(
    vi.fn(),
  );
});
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  const onReview = vi.fn();
  const view = render(
    <QueryClientProvider client={client}>
      <WorkspaceRecognitionActivity project={source} onReview={onReview} />
    </QueryClientProvider>,
  );
  return { ...view, onReview, client };
}
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.restoreAllMocks();
});

it('reads an existing job from a cold workspace and navigates through its owner callback', async () => {
  const status = vi
    .spyOn(preparationApi, 'getLayerRecognitionStatus')
    .mockResolvedValue(response('running'));
  const discovery = vi.spyOn(preparationApi, 'getLayerRecognition');
  const resume = vi.spyOn(preparationApi, 'retryLayerRecognition');
  const { onReview } = mount();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Завершаем распознавание' }),
  );
  expect(status).toHaveBeenCalledOnce();
  expect(onReview).toHaveBeenCalledOnce();
  expect(discovery).not.toHaveBeenCalled();
  expect(resume).not.toHaveBeenCalled();
});

it('does not start a recognition job when the status endpoint has none', async () => {
  const status = vi
    .spyOn(preparationApi, 'getLayerRecognitionStatus')
    .mockResolvedValue(null);
  const discovery = vi.spyOn(preparationApi, 'getLayerRecognition');
  const { container, client } = mount();
  await waitFor(() =>
    expect(
      client.getQueryData(
        recognitionKey({ projectId: 'native', source: sourceIdentity(source) }),
      ),
    ).toBeNull(),
  );
  expect(status).toHaveBeenCalledOnce();
  expect(container).toBeEmptyDOMElement();
  expect(discovery).not.toHaveBeenCalled();
});

it('keeps failed status reads visible without starting or resuming AI', async () => {
  vi.spyOn(preparationApi, 'getLayerRecognitionStatus').mockRejectedValue(
    new Error('Lost reply'),
  );
  const discovery = vi.spyOn(preparationApi, 'getLayerRecognition');
  const resume = vi.spyOn(preparationApi, 'retryLayerRecognition');
  const { onReview } = mount();
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Статус распознавания недоступен',
    }),
  );
  expect(onReview).toHaveBeenCalledOnce();
  expect(discovery).not.toHaveBeenCalled();
  expect(resume).not.toHaveBeenCalled();
});

it('shares completion with the source document instead of creating a second state store', async () => {
  vi.spyOn(preparationApi, 'getLayerRecognitionStatus').mockResolvedValue(
    response('running'),
  );
  const { client } = mount();
  await screen.findByRole('button', { name: 'Завершаем распознавание' });
  act(() =>
    client.setQueryData(
      recognitionKey({ projectId: 'native', source: sourceIdentity(source) }),
      response('completed'),
    ),
  );
  expect(
    await screen.findByRole('button', { name: 'Предложения по слоям готовы' }),
  ).toBeVisible();
});

it.each([
  ['reviewing_conflicts', 'Уточняем спорные назначения: 3 из 8'],
  ['saving', 'Сохраняем предложения'],
] as const)(
  'shows the active %s stage from the same observed job',
  async (stage, label) => {
    vi.spyOn(preparationApi, 'getLayerRecognitionStatus').mockResolvedValue({
      ...response('running'),
      stage,
      stage_processed_count: 3,
      stage_total_count: 8,
    });
    const resume = vi.spyOn(preparationApi, 'retryLayerRecognition');
    mount();
    expect(await screen.findByRole('button', { name: label })).toBeEnabled();
    expect(resume).not.toHaveBeenCalled();
  },
);
