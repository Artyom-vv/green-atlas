import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ViewportLoading } from './ViewportLoading';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it('keeps one header caption and one elapsed clock across real stage changes', () => {
  vi.useFakeTimers();
  const header = document.createElement('div');
  const progress = document.createElement('div');
  document.body.append(header, progress);
  const view = render(
    <ViewportLoading
      label="Подготовка сцены"
      headerSlot={header}
      progressSlot={progress}
    />,
  );
  expect(screen.getAllByRole('status')).toHaveLength(1);
  expect(view.container).toBeEmptyDOMElement();
  expect(document.querySelector('[data-slot=viewport-loading]')).toBeNull();
  expect(header).toHaveTextContent('Подготовка сцены0 с');
  expect(progress.querySelector('[data-slot=scene-pending]')).not.toBeNull();
  act(() => vi.advanceTimersByTime(5000));
  view.rerender(
    <ViewportLoading
      label="Загрузка моделей"
      headerSlot={header}
      progressSlot={progress}
    />,
  );
  expect(header).toHaveTextContent('Загрузка моделей5 с');
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  view.unmount();
  expect(header).toBeEmptyDOMElement();
  expect(progress).toBeEmptyDOMElement();
  expect(vi.getTimerCount()).toBe(0);
  header.remove();
  progress.remove();
});
