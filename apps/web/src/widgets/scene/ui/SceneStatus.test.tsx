import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SceneStatus } from './SceneStatus';

afterEach(cleanup);

describe('SceneStatus', () => {
  it('pairs a renderer failure with renderer recovery when the scene request also failed', () => {
    const onRetry = vi.fn();
    const onRetryScene = vi.fn();
    render(
      <SceneStatus
        horizon={40}
        snapshotHorizon={20}
        modelsReady
        error="Не удалось получить новый прогноз"
        renderError="Графический контекст недоступен"
        onRetry={onRetry}
        onRetryScene={onRetryScene}
      />,
    );

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Не удалось запустить 3D');
    expect(alert).toHaveTextContent('Графический контекст недоступен');
    expect(alert).not.toHaveTextContent('Не удалось получить новый прогноз');
    expect(alert).not.toHaveTextContent('На сцене:');
    expect(screen.getAllByRole('button')).toHaveLength(1);
    fireEvent.click(
      screen.getByRole('button', { name: 'Повторить запуск 3D' }),
    );
    expect(onRetry).toHaveBeenCalledOnce();
    expect(onRetryScene).not.toHaveBeenCalled();
  });

  it('keeps the displayed year visible and retries the scene query after a forecast request fails', () => {
    const onRetry = vi.fn();
    const onRetryScene = vi.fn();
    const props = {
      horizon: 40,
      snapshotHorizon: 20,
      modelsReady: true,
      onRetry,
      onRetryScene,
    };
    const view = render(
      <SceneStatus {...props} error="Сервер временно недоступен" />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Не удалось обновить сцену',
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      'На сцене: через 20 лет. Запрошено: через 40 лет',
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Повторить загрузку сцены' }),
    );
    expect(onRetryScene).toHaveBeenCalledOnce();
    expect(onRetry).not.toHaveBeenCalled();

    view.rerender(<SceneStatus {...props} loading />);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Обновляем прогноз');
    expect(screen.getByRole('status')).toHaveTextContent(
      'На сцене: через 20 лет. Запрошено: через 40 лет',
    );

    view.rerender(<SceneStatus {...props} snapshotHorizon={40} />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('does not offer renderer recovery for a query failure without a query retry callback', () => {
    render(
      <SceneStatus
        horizon={20}
        modelsReady
        error="Сведения о сцене недоступны"
        onRetry={vi.fn()}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Сведения о сцене недоступны',
    );
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByText(/На сцене:/)).not.toBeInTheDocument();
  });

  it('does not call a snapshot displayed before its plant models are ready', () => {
    render(
      <SceneStatus
        horizon={40}
        snapshotHorizon={20}
        modelsReady={false}
        loading
        onRetry={vi.fn()}
      />,
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      'Загружаем модели растений',
    );
    expect(screen.queryByText(/На сцене:/)).not.toBeInTheDocument();
  });
});
