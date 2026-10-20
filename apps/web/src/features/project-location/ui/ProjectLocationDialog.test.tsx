import {
  cleanup,
  fireEvent,
  render as renderView,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import {
  locationAlignment,
  locationDraft,
  locationState,
  locationSuggestion,
} from '../model/locationFixtures';
import { locationActions } from '../model/useProjectLocation';
import { ProjectLocationDialog } from './ProjectLocationDialog';
import { locationServiceSettingsActions } from '../model/locationServiceSettingsActions';

function render(element: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderView(
    <QueryClientProvider client={client}>{element}</QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.spyOn(locationActions, 'read').mockResolvedValue({
    ...locationState,
    draft: locationDraft,
  });
  vi.spyOn(locationActions, 'align').mockResolvedValue(locationAlignment);
  vi.spyOn(locationActions, 'apply').mockResolvedValue({
    ...locationState,
    applied: {
      status: 'candidate',
      draft_id: locationDraft.id,
      alignment_id: locationAlignment.id,
      source_sha256: locationDraft.source_sha256,
      geometry_version: locationDraft.geometry_version,
    },
  });
  vi.spyOn(locationActions, 'resolve').mockResolvedValue(locationDraft);
  vi.spyOn(locationActions, 'suggest').mockResolvedValue({
    configured: true,
    suggestions: [locationSuggestion],
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
function open(onClose = vi.fn(), onApplied = vi.fn()) {
  render(
    <ProjectLocationDialog
      projectId="project"
      open
      onClose={onClose}
      onApplied={onApplied}
    />,
  );
  return { onClose, onApplied };
}
async function manual() {
  fireEvent.click(
    await screen.findByRole('button', { name: 'Скорректировать' }),
  );
  return {
    cad: screen.getByRole('application', { name: 'План: CAD' }),
    map: screen.getByRole('application', { name: 'План: OSM' }),
  };
}

it('automatically checks a loaded draft and only applies after explicit confirmation', async () => {
  const { onApplied, onClose } = open();
  const apply = await screen.findByRole('button', { name: 'Применить' });
  expect(
    screen.getByRole('application', { name: 'План: Совмещение CAD и OSM' }),
  ).toBeVisible();
  expect(locationActions.align).toHaveBeenCalledOnce();
  expect(locationActions.apply).not.toHaveBeenCalled();
  fireEvent.click(apply);
  await waitFor(() => expect(onApplied).toHaveBeenCalledOnce());
  expect(onClose).toHaveBeenCalledOnce();
});

it('keeps a failed application visible and does not announce or close it as saved', async () => {
  vi.mocked(locationActions.apply).mockRejectedValueOnce(
    new Error('Диск недоступен'),
  );
  const { onApplied, onClose } = open();
  fireEvent.click(await screen.findByRole('button', { name: 'Применить' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Диск недоступен');
  expect(onApplied).not.toHaveBeenCalled();
  expect(onClose).not.toHaveBeenCalled();
});

it('uses four explicit pairs, an independent control and server checking before review', async () => {
  open();
  const { cad, map } = await manual();
  for (let index = 0; index < 4; index++) {
    fireEvent.keyDown(cad, { key: 'ArrowRight' });
    fireEvent.keyDown(cad, { key: 'Enter' });
    fireEvent.keyDown(map, { key: 'ArrowUp' });
    fireEvent.keyDown(map, { key: 'Enter' });
    if (index < 3)
      fireEvent.click(screen.getByRole('button', { name: 'Следующая пара' }));
  }
  expect(screen.getByText('Контрольная пара')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Проверить совмещение' }));
  await screen.findByRole('button', { name: 'Применить' });
  const body = vi.mocked(locationActions.align).mock.calls.at(-1)![1];
  expect(body.control_pairs?.map((pair) => pair.usage)).toEqual([
    'fit',
    'fit',
    'fit',
    'holdout',
  ]);
  expect(
    body.control_pairs?.every(
      (pair) =>
        Math.abs(pair.lon_lat[0]) <= 180 && Math.abs(pair.lon_lat[1]) <= 85,
    ),
  ).toBe(true);
  expect(locationActions.apply).not.toHaveBeenCalled();
});

it('requires confirmation on close and preserves a placed point when cancellation is declined', async () => {
  const { onClose } = open();
  const { cad } = await manual();
  fireEvent.keyDown(cad, { key: 'Enter' });
  fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
  const guard = await screen.findByRole('dialog', {
    name: 'Отменить корректировку?',
  });
  expect(within(guard).getByText('1')).toBeVisible();
  fireEvent.click(
    within(guard).getByRole('button', { name: 'Продолжить работу' }),
  );
  expect(onClose).not.toHaveBeenCalled();
  expect(
    screen.getByRole('button', { name: 'Изменить точку на чертеже' }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
  fireEvent.click(
    await screen.findByRole('button', { name: 'Отменить корректировку' }),
  );
  expect(onClose).toHaveBeenCalledOnce();
  expect(locationActions.apply).not.toHaveBeenCalled();
});

it('does not clear unsaved points or resolve a new address before confirmation', async () => {
  vi.mocked(locationActions.align).mockResolvedValueOnce({
    ...locationAlignment,
    status: 'needs_controls',
  });
  open();
  const cad = await screen.findByRole('application', { name: 'План: CAD' });
  fireEvent.keyDown(cad, { key: 'Enter' });
  fireEvent.click(screen.getByRole('button', { name: 'Изменить адрес' }));
  const input = await screen.findByRole('combobox', { name: 'Адрес' });
  fireEvent.change(input, { target: { value: 'Москва новая улица' } });
  await screen.findByRole('option');
  fireEvent.keyDown(input, { key: 'Enter' });
  const guard = await screen.findByRole('dialog', {
    name: 'Отменить корректировку?',
  });
  expect(locationActions.resolve).not.toHaveBeenCalled();
  fireEvent.click(
    within(guard).getByRole('button', { name: 'Продолжить работу' }),
  );
  expect(
    screen.getByRole('button', { name: 'Изменить точку на чертеже' }),
  ).toBeVisible();
  expect(locationActions.resolve).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Изменить адрес' }));
  fireEvent.change(screen.getByRole('combobox', { name: 'Адрес' }), {
    target: { value: 'Москва новая улица' },
  });
  await screen.findByRole('option');
  fireEvent.keyDown(screen.getByRole('combobox', { name: 'Адрес' }), {
    key: 'Enter',
  });
  fireEvent.click(
    await screen.findByRole('button', { name: 'Отменить корректировку' }),
  );
  await screen.findByRole('button', { name: 'Применить' });
  expect(locationActions.resolve).toHaveBeenCalledOnce();
  expect(locationActions.apply).not.toHaveBeenCalled();
});

it('offers one address field and passes a selected canonical street without a city step', async () => {
  vi.mocked(locationActions.read).mockResolvedValue(locationState);
  const { onClose } = open();
  const input = await screen.findByRole('combobox', { name: 'Адрес' });
  expect(
    screen.queryByRole('combobox', { name: 'Город' }),
  ).not.toBeInTheDocument();
  fireEvent.change(input, { target: { value: 'Москва Парковая' } });
  await screen.findByRole('option');
  fireEvent.keyDown(input, { key: 'Enter' });
  await screen.findByRole('button', { name: 'Применить' });
  expect(vi.mocked(locationActions.suggest).mock.calls[0][1]).toEqual({
    query: 'Москва Парковая',
    kind: 'street',
  });
  expect(vi.mocked(locationActions.resolve).mock.calls[0][1]).toEqual({
    suggestion: locationSuggestion,
  });
  expect(onClose).not.toHaveBeenCalled();
});

it('updates address configuration and keeps the entered query without reloading the project', async () => {
  vi.mocked(locationActions.read).mockResolvedValue({
    ...locationState,
    configured: false,
    message: 'Поиск адресов не настроен',
  });
  const configured = {
    configured: true,
    source: 'personal' as const,
    personal_key_configured: true,
    bundled_key_configured: true,
  };
  vi.spyOn(locationServiceSettingsActions, 'read').mockResolvedValue({
    ...configured,
    configured: false,
    source: 'none',
    personal_key_configured: false,
    bundled_key_configured: false,
  });
  vi.spyOn(locationServiceSettingsActions, 'save').mockResolvedValue(
    configured,
  );
  open();
  const address = await screen.findByRole('combobox', { name: 'Адрес' });
  fireEvent.change(address, { target: { value: 'Москва Парковая' } });
  fireEvent.keyDown(address, { key: 'Escape' });
  fireEvent.click(
    screen.getByRole('button', { name: 'Настройки поиска адресов' }),
  );
  fireEvent.change(await screen.findByLabelText('Личный API-ключ DaData'), {
    target: { value: 'new-key' },
  });
  fireEvent.submit(screen.getByRole('form', { name: 'Ключ DaData' }));
  await screen.findByText('Ключ сохранён');
  expect(address).toHaveValue('Москва Парковая');
  expect(
    screen.queryByText('Поиск адресов не настроен'),
  ).not.toBeInTheDocument();
  expect(locationActions.read).toHaveBeenCalledOnce();
  expect(locationActions.resolve).not.toHaveBeenCalled();
});

it('preserves unsaved manual alignment while changing the address key', async () => {
  vi.mocked(locationActions.align).mockResolvedValueOnce({
    ...locationAlignment,
    status: 'needs_controls',
  });
  const configured = {
    configured: true,
    source: 'personal' as const,
    personal_key_configured: true,
    bundled_key_configured: true,
  };
  vi.spyOn(locationServiceSettingsActions, 'read').mockResolvedValue(
    configured,
  );
  vi.spyOn(locationServiceSettingsActions, 'save').mockResolvedValue(
    configured,
  );
  open();
  const cad = await screen.findByRole('application', { name: 'План: CAD' });
  fireEvent.keyDown(cad, { key: 'Enter' });
  fireEvent.click(screen.getByRole('button', { name: 'Изменить адрес' }));
  fireEvent.click(
    screen.getByRole('button', { name: 'Настройки поиска адресов' }),
  );
  fireEvent.change(await screen.findByLabelText('Личный API-ключ DaData'), {
    target: { value: 'replacement-key' },
  });
  fireEvent.submit(screen.getByRole('form', { name: 'Ключ DaData' }));
  await screen.findByText('Ключ сохранён');
  fireEvent.click(
    screen.getByRole('button', { name: 'Продолжить корректировку' }),
  );
  expect(
    screen.getByRole('button', { name: 'Изменить точку на чертеже' }),
  ).toBeVisible();
  expect(locationActions.read).toHaveBeenCalledOnce();
  expect(locationActions.align).toHaveBeenCalledOnce();
  expect(locationActions.resolve).not.toHaveBeenCalled();
});

it('keeps valid previous views and supports retry after a map failure', async () => {
  vi.mocked(locationActions.read).mockResolvedValue(locationState);
  vi.mocked(locationActions.resolve).mockRejectedValueOnce(
    new Error('Карта временно недоступна'),
  );
  open();
  const input = await screen.findByRole('combobox', { name: 'Адрес' });
  fireEvent.change(input, { target: { value: 'Парковая' } });
  await screen.findByRole('option');
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Карта временно недоступна',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
  await screen.findByRole('button', { name: 'Применить' });
  expect(locationActions.resolve).toHaveBeenCalledTimes(2);
  expect(locationActions.apply).not.toHaveBeenCalled();
});

it('preserves manual center while choosing a bounded ambiguous street segment', async () => {
  const suggestion = { ...locationSuggestion, longitude: null, latitude: null };
  vi.mocked(locationActions.read).mockResolvedValue({
    ...locationState,
    draft: {
      ...locationDraft,
      suggestion,
      status: 'no_coordinates',
      street_candidates: [],
    },
  });
  vi.mocked(locationActions.resolve)
    .mockResolvedValueOnce({
      ...locationDraft,
      suggestion,
      status: 'ambiguous_street',
      selected_street_id: null,
      street_candidates: [
        { ...locationDraft.street_candidates[0], id: 'first' },
        { ...locationDraft.street_candidates[0], id: 'second' },
      ],
    })
    .mockResolvedValueOnce(locationDraft);
  open();
  fireEvent.change(await screen.findByRole('textbox', { name: 'Долгота' }), {
    target: { value: '37.6' },
  });
  fireEvent.change(screen.getByRole('textbox', { name: 'Широта' }), {
    target: { value: '55.7' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Загрузить участок' }));
  await screen.findByRole('application', { name: 'План: Участки улицы OSM' });
  fireEvent.click(await screen.findByRole('button', { name: 'Участок улицы' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Участок 2' }));
  fireEvent.keyDown(
    screen.getByRole('application', { name: 'План: Участки улицы OSM' }),
    { key: 'Enter' },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Загрузить участок' }));
  await screen.findByRole('button', { name: 'Применить' });
  const next = vi.mocked(locationActions.resolve).mock.calls[1][1];
  expect(next.center_lon_lat).toEqual([37.6, 55.7]);
  expect(next.street_candidate_id).toBe('second');
  expect(next.section_bbox).toHaveLength(4);
});
