import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Button, ScrollArea } from '@green/ui';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ResultPanel } from './ResultPanel';

afterEach(cleanup);

describe('ResultPanel', () => {
  it('keeps its heading and actions outside the scrolling result content', () => {
    const undo = vi.fn();
    const { container } = render(
      <ResultPanel
        title="История"
        count={0}
        actions={<Button onClick={undo}>Отменить</Button>}
      >
        <p>Изменений пока нет</p>
      </ResultPanel>,
    );
    const viewport = container.querySelector('[data-slot="scroll-viewport"]');
    expect(viewport).toContainElement(screen.getByText('Изменений пока нет'));
    expect(viewport).not.toContainElement(screen.getByRole('heading'));
    expect(viewport).not.toContainElement(screen.getByRole('button'));
    expect(screen.getByText('0')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Отменить' }));
    expect(undo).toHaveBeenCalledOnce();
  });

  it('accepts a body with its own scrollport without wrapping it in another', () => {
    const { container } = render(
      <ResultPanel
        title="Ведомость"
        body={
          <>
            <label>
              Поиск
              <input />
            </label>
            <ScrollArea>
              <p>Липа</p>
            </ScrollArea>
          </>
        }
      />,
    );
    const viewports = container.querySelectorAll(
      '[data-slot="scroll-viewport"]',
    );
    expect(viewports).toHaveLength(1);
    expect(viewports[0]).toContainElement(screen.getByText('Липа'));
    expect(viewports[0]).not.toContainElement(screen.getByRole('textbox'));
  });
});
