import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Panel, PanelHeader, Surface } from '../index';

describe('surface composition', () => {
  it('provides standard headers but accepts replacement and removal without wrapping content again', () => {
    const { rerender } = render(
      <Panel title="Настройки" description="Параметры">
        Тело
      </Panel>,
    );
    expect(
      screen.getByRole('heading', { name: 'Настройки' }),
    ).toBeInTheDocument();
    rerender(
      <Panel
        title="Настройки"
        header={
          <PanelHeader>
            <h2>Особая задача</h2>
          </PanelHeader>
        }
      >
        Тело
      </Panel>,
    );
    expect(
      screen.queryByRole('heading', { name: 'Настройки' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'Особая задача' }),
    ).toBeInTheDocument();
    rerender(
      <Panel title="Настройки" header={null}>
        Тело
      </Panel>,
    );
    expect(screen.queryByRole('heading')).not.toBeInTheDocument();
  });
  it('keeps the simple surface padding-free for caller-owned composition', () => {
    const { container } = render(
      <Surface>
        <div data-testid="body">Содержимое</div>
      </Surface>,
    );
    expect(container.firstElementChild?.firstElementChild).toBe(
      screen.getByTestId('body'),
    );
  });
});
