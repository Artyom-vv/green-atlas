import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import type { Layer } from '@green/api-client';
import { afterEach, expect, it, vi } from 'vitest';
import type { ComponentProps } from 'react';
import { SourceRecognitionIndicator } from './SourceRecognitionIndicator';

afterEach(cleanup);
const baseLayer = {
  suggested_kind: 'building',
  mapped_kind: 'building',
  object_count: 1,
  color: '#475569',
  linetype: 'CONTINUOUS',
  geometry_complete: true,
  required: false,
  visible: true,
} satisfies Partial<Layer>;
const layers: Layer[] = [
  {
    ...baseLayer,
    id: 'building',
    source_name: 'Здание',
    mapping_confirmed: false,
  },
  {
    ...baseLayer,
    id: 'boundary',
    source_name: 'Граница',
    mapping_confirmed: true,
  },
];
function setup(
  changes: Partial<ComponentProps<typeof SourceRecognitionIndicator>> = {},
) {
  const props = {
    layers,
    mappings: {
      building: {
        layer_id: 'building',
        kind: 'building',
        confirmed: true,
        visible: true,
      },
    },
    allSavedLayersConfirmed: false,
    layerRecognition: {
      source_sha256: null,
      provider: 'local-rules/1',
      stage_processed_count: 0,
      stage_total_count: 0,
      status: 'failed',
      message: 'Сервер не выполнил запрос (HTTP 422)',
      categories: [],
      proposals: [],
      processed_count: 1,
      total_count: 2,
    },
    layerRecognitionQuery: { isLoading: false, isError: false },
    retryLayerRecognition: {
      isError: false,
      isPending: false,
      checkingStatus: false,
      noKnownJob: false,
      mutate: vi.fn(),
    },
    onReview: vi.fn(),
    ...changes,
  } as ComponentProps<typeof SourceRecognitionIndicator>;
  const view = render(<SourceRecognitionIndicator {...props} />);
  return {
    ...props,
    rerender: (
      next: Partial<ComponentProps<typeof SourceRecognitionIndicator>>,
    ) => view.rerender(<SourceRecognitionIndicator {...props} {...next} />),
  };
}

it('shows checkpointed proposals during recognition and preserves the summary while verifying persistence', async () => {
  const mappings = {
    building: {
      layer_id: 'building',
      kind: 'building' as const,
      confirmed: false,
      visible: true,
    },
    boundary: {
      layer_id: 'boundary',
      kind: 'site_border' as const,
      confirmed: false,
      visible: true,
    },
  };
  const recognitionMappings = {
    ...mappings,
    building: { ...mappings.building, confirmed: true },
  };
  const props = setup({
    mappings,
    recognitionMappings,
    layerRecognition: {
      source_sha256: 'source',
      provider: 'fixture',
      status: 'running',
      stage: 'classifying',
      processed_count: 1,
      total_count: 2,
      stage_processed_count: 1,
      stage_total_count: 2,
      completed_layer_ids: ['building'],
      categories: [],
      proposals: [],
    },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Слои: распознавание' }));
  expect(await screen.findByText('Определено')).toBeVisible();
  expect(screen.getByText('1 из 2', { selector: 'dd' })).toBeVisible();
  expect(
    screen.getByText('Проверить', { selector: 'dt' }).nextElementSibling,
  ).toHaveTextContent('0');
  props.rerender({
    autoApplyingRecognition: true,
    layerRecognition: {
      ...props.layerRecognition!,
      status: 'completed',
      processed_count: 2,
    },
  });
  expect(
    screen.getByRole('button', { name: 'Слои: распознавание' }),
  ).toHaveAttribute('data-state', 'saving');
  expect(
    screen.getByRole('progressbar', { name: 'Сохраняем назначения слоёв' }),
  ).toBeVisible();
  expect(screen.queryByText('Распознавание завершено')).not.toBeInTheDocument();
  const retry = vi.fn();
  props.rerender({
    autoApplyingRecognition: false,
    layerRecognition: {
      ...props.layerRecognition!,
      status: 'completed',
      processed_count: 2,
    },
    recognitionSaveRecovery: { loading: false, onRetry: retry },
  });
  expect(
    screen.getByRole('button', { name: 'Слои: распознавание' }),
  ).toHaveAttribute('data-state', 'unknown');
  fireEvent.click(screen.getByRole('button', { name: 'Обновить статус' }));
  expect(retry).toHaveBeenCalledOnce();
  expect(props.retryLayerRecognition.mutate).not.toHaveBeenCalled();
});

it('counts review only among processed layers and keeps the full source denominator', async () => {
  const sourceLayers = Array.from({ length: 539 }, (_, index): Layer => ({
    ...baseLayer,
    id: `layer-${index}`,
    source_name: `Слой ${index}`,
    mapping_confirmed: false,
  }));
  const mappings = Object.fromEntries(
    sourceLayers.map((layer) => [
      layer.id,
      {
        layer_id: layer.id,
        kind: 'building' as const,
        confirmed: false,
        visible: true,
      },
    ]),
  );
  const recognitionMappings = Object.fromEntries(
    sourceLayers.map((layer, index) => [
      layer.id,
      { ...mappings[layer.id], confirmed: index < 23 },
    ]),
  );
  const recognition = {
    source_sha256: 'source',
    provider: 'fixture',
    status: 'running' as const,
    stage: 'classifying' as const,
    processed_count: 24,
    total_count: 539,
    stage_processed_count: 24,
    stage_total_count: 539,
    completed_layer_ids: sourceLayers.slice(0, 24).map((layer) => layer.id),
    categories: [],
    proposals: [],
  };
  const props = setup({
    layers: sourceLayers,
    mappings,
    recognitionMappings,
    layerRecognition: recognition,
  });
  fireEvent.click(screen.getByRole('button', { name: 'Слои: распознавание' }));
  expect(
    await screen.findByText('23 из 539', { selector: 'dd' }),
  ).toBeVisible();
  expect(
    screen.getByText('Проверить', { selector: 'dt' }).nextElementSibling,
  ).toHaveTextContent('1');
  props.rerender({
    layerRecognition: {
      ...recognition,
      completed_layer_ids: [],
      processed_count: 0,
      stage_processed_count: 0,
    },
  });
  expect(screen.getByText('0 из 539', { selector: 'dd' })).toBeVisible();
  expect(
    screen.getByText('Проверить', { selector: 'dt' }).nextElementSibling,
  ).toHaveTextContent('0');
  props.rerender({
    layerRecognition: {
      ...recognition,
      completed_layer_ids: ['layer-0', 'layer-0'],
      processed_count: 2,
    },
  });
  expect(screen.getByText('0 из 539', { selector: 'dd' })).toBeVisible();
  expect(
    screen.getByText('Проверить', { selector: 'dt' }).nextElementSibling,
  ).toHaveTextContent('0');
  props.rerender({
    layerRecognition: {
      ...recognition,
      status: 'completed',
      processed_count: 539,
    },
  });
  expect(screen.getByText('23 из 539', { selector: 'dd' })).toBeVisible();
  expect(
    screen.getByText('Проверить', { selector: 'dt' }).nextElementSibling,
  ).toHaveTextContent('516');
});

it('opens one summary with draft counts and keeps server diagnostics contextual', async () => {
  setup();
  expect(screen.queryByText('Обработано')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Слои: распознавание' }));
  expect(await screen.findByText('Принято')).toBeVisible();
  expect(screen.queryByText('Обработано')).not.toBeInTheDocument();
  expect(screen.getByText('2 из 2')).toBeVisible();
  const detail = screen.getByText('Сервер не выполнил запрос (HTTP 422)');
  expect(detail).not.toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Подробности' }));
  expect(detail).toBeVisible();
});

it('keeps the summary available after completion and closes it when navigating to layers', async () => {
  const props = setup({
    layerRecognition: {
      source_sha256: null,
      provider: 'local-rules/1',
      stage: 'saving',
      stage_processed_count: 1,
      stage_total_count: 1,
      status: 'completed',
      proposals: [],
      categories: [],
      processed_count: 2,
      total_count: 2,
    } as ComponentProps<typeof SourceRecognitionIndicator>['layerRecognition'],
  });
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  const trigger = screen.getByRole('button', { name: 'Слои: распознавание' });
  expect(trigger).toHaveTextContent(/^Слои$/);
  expect(trigger).toHaveAccessibleDescription('Слои распознаны. 2 из 2');
  fireEvent.click(trigger);
  expect(await screen.findByText('Распознано')).toBeVisible();
  expect(screen.getByText('Распознано').nextElementSibling).toHaveTextContent(
    '2 из 2',
  );
  const review = await screen.findByRole('button', { name: 'Проверить слои' });
  expect(screen.queryByText('1 из 2')).not.toBeInTheDocument();
  expect(
    screen.getByText('Принято').compareDocumentPosition(review) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  fireEvent.click(review);
  expect(props.onReview).toHaveBeenCalledOnce();
  await waitFor(() =>
    expect(screen.queryByText('Принято')).not.toBeInTheDocument(),
  );
});

it('retains assignment information even when confirmed layers have no recognition job', async () => {
  setup({ allSavedLayersConfirmed: true, layerRecognition: undefined });
  const trigger = screen.getByRole('button', { name: 'Слои: распознавание' });
  expect(trigger).toHaveTextContent(/^Слои$/);
  expect(trigger).toHaveAccessibleDescription('Назначения слоёв сохранены');
  fireEvent.click(trigger);
  expect(await screen.findByText('Принято')).toBeVisible();
  expect(screen.queryByText('Распознано')).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Продолжить' }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Открыть слои' })).toBeVisible();
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
});

it('keeps a compact label while announcing each processing phase', () => {
  const props = setup({
    layerRecognition: undefined,
    layerRecognitionQuery: {
      isLoading: true,
      isError: false,
    } as ComponentProps<
      typeof SourceRecognitionIndicator
    >['layerRecognitionQuery'],
  });
  const trigger = screen.getByRole('button', { name: 'Слои: распознавание' });
  expect(trigger).toHaveTextContent(/^Слои$/);
  expect(trigger).toHaveAccessibleDescription('Получаем статус');
  expect(screen.getByRole('status')).toHaveTextContent('Получаем статус');
  expect(trigger).toHaveAttribute('data-state', 'loading');
  expect(trigger).not.toBeDisabled();
  const recognition = {
    ...props.layerRecognition,
    status: 'running',
    stage: 'classifying',
    provider: 'fixture',
    categories: [],
    proposals: [],
    source_sha256: null,
    processed_count: 1,
    total_count: 2,
    stage_processed_count: 1,
    stage_total_count: 2,
  } as NonNullable<
    ComponentProps<typeof SourceRecognitionIndicator>['layerRecognition']
  >;
  const query = { isLoading: false, isError: false } as ComponentProps<
    typeof SourceRecognitionIndicator
  >['layerRecognitionQuery'];
  props.rerender({
    layerRecognition: recognition,
    layerRecognitionQuery: query,
  });
  expect(trigger).toHaveTextContent(/^Слои$/);
  expect(trigger).toHaveAccessibleDescription('Распознаём. 1 из 2');
  expect(screen.getByRole('status')).toHaveTextContent('Распознаём. 1 из 2');
  props.rerender({
    layerRecognition: {
      ...recognition,
      stage: 'reviewing_conflicts',
      processed_count: 2,
      stage_processed_count: 0,
      stage_total_count: 1,
    },
    layerRecognitionQuery: query,
  });
  expect(trigger).toHaveTextContent(/^Слои$/);
  expect(trigger).toHaveAccessibleDescription('Уточняем. 0 из 1');
  expect(screen.getByRole('status')).toHaveTextContent('Уточняем. 0 из 1');
  props.rerender({
    layerRecognition: {
      ...recognition,
      stage: 'saving',
      processed_count: 2,
      stage_processed_count: 0,
      stage_total_count: 1,
    },
    layerRecognitionQuery: query,
  });
  expect(trigger).toHaveTextContent(/^Слои$/);
  expect(trigger).toHaveAccessibleDescription('Сохраняем результат');
  expect(screen.getByRole('status')).toHaveTextContent('Сохраняем результат');
  expect(trigger).not.toHaveTextContent('/');
  props.rerender({
    layerRecognition: {
      ...recognition,
      stage: 'saving',
      status: 'completed',
      processed_count: 2,
      stage_processed_count: 1,
      stage_total_count: 1,
    },
    layerRecognitionQuery: query,
  });
  expect(trigger).toHaveTextContent(/^Слои$/);
  expect(trigger).toHaveAccessibleDescription('Слои распознаны. 2 из 2');
  expect(screen.getByRole('status')).toHaveTextContent('Слои распознаны. 2 из 2');
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  expect(props.retryLayerRecognition.mutate).not.toHaveBeenCalled();
});

it('shows a pending read on the trigger even with a cached completed result', async () => {
  setup({
    layerRecognition: {
      source_sha256: null,
      provider: 'fixture',
      status: 'completed',
      processed_count: 2,
      total_count: 2,
      proposals: [],
      categories: [],
      stage_processed_count: 1,
      stage_total_count: 1,
    },
    layerRecognitionQuery: {
      isLoading: false,
      isFetching: true,
      isError: false,
    } as ComponentProps<
      typeof SourceRecognitionIndicator
    >['layerRecognitionQuery'],
  });
  const trigger = screen.getByRole('button', { name: 'Слои: распознавание' });
  expect(trigger).toHaveTextContent(/^Слои$/);
  expect(trigger).toHaveAccessibleDescription('Проверяем статус');
  expect(trigger).toHaveAttribute('data-state', 'checking');
  expect(trigger).not.toBeDisabled();
  fireEvent.click(trigger);
  await waitFor(() =>
    expect(
      screen.getByRole('progressbar', {
        name: 'Проверяем состояние распознавания',
      }),
    ).toBeVisible(),
  );
  expect(
    screen.queryByRole('button', { name: 'Повторить' }),
  ).not.toBeInTheDocument();
});
