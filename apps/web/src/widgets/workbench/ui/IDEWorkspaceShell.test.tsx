import { WorkbenchContribution } from '@/shared/layout/workbenchSlots';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { useState } from 'react';
import { FormProvider, useForm, useFormContext } from 'react-hook-form';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { IDE_WORKSPACE_STORAGE_KEY } from '../model/ideWorkspaceLayout';
import {
  IDEWorkspaceShell,
  type IDEWorkspaceShellProps,
} from './IDEWorkspaceShell';

function Draft({ label }: { label: string }) {
  const [value, setValue] = useState('');
  return (
    <input
      aria-label={label}
      value={value}
      onChange={(event) => setValue(event.target.value)}
    />
  );
}
const slots = (): IDEWorkspaceShellProps => ({
  header: <div>Проект</div>,
  map: <div>Карта</div>,
  resources: <Draft label="Поиск слоёв" />,
  inspector: <Draft label="Свойство" />,
  tool: <Draft label="Параметр ряда" />,
  assistant: <Draft label="Сообщение" />,
  results: {
    checks: <Draft label="Фильтр проверок" />,
    schedule: <div>Ведомость проекта</div>,
    history: <div>История проекта</div>,
  },
});

beforeEach(() => {
  localStorage.clear();
  Object.defineProperty(window, 'innerWidth', {
    configurable: true,
    value: 1280,
  });
  Object.defineProperty(window, 'innerHeight', {
    configurable: true,
    value: 800,
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('IDE workspace slot lifecycle', () => {
  it('keeps the originating form context and DOM host when a contribution is resized', () => {
    function Field() {
      const { register } = useFormContext<{ count: string }>();
      return <input aria-label="Портал формы" {...register('count')} />;
    }
    function Scenario() {
      const form = useForm({ defaultValues: { count: '40' } });
      return (
        <FormProvider {...form}>
          <WorkbenchContribution slot="tool">
            <Field />
          </WorkbenchContribution>
        </FormProvider>
      );
    }
    render(
      <IDEWorkspaceShell {...slots()} tool={null} initialRightTab="tool">
        <Scenario />
      </IDEWorkspaceShell>,
    );
    const input = screen.getByRole('textbox', { name: 'Портал формы' });
    const host = input.parentElement;
    fireEvent.change(input, { target: { value: '72' } });
    fireEvent.keyDown(
      screen.getByRole('separator', { name: 'Ширина правой области' }),
      { key: 'ArrowLeft' },
    );
    fireEvent.click(screen.getByRole('tab', { name: 'Свойства' }));
    fireEvent.click(screen.getByRole('tab', { name: 'Инструмент' }));
    expect(screen.getByRole('textbox', { name: 'Портал формы' })).toBe(input);
    expect(input.parentElement).toBe(host);
    expect(input).toHaveValue('72');
  });
  it('preserves a tool draft through tab switches and closing the right pane', () => {
    render(<IDEWorkspaceShell {...slots()} initialRightTab="tool" />);
    const input = screen.getByRole('textbox', { name: 'Параметр ряда' });
    fireEvent.change(input, { target: { value: '7.5' } });
    fireEvent.click(screen.getByRole('tab', { name: 'Свойства' }));
    expect(input).not.toBeVisible();
    fireEvent.click(screen.getByRole('tab', { name: 'Инструмент' }));
    expect(screen.getByRole('textbox', { name: 'Параметр ряда' })).toBe(input);
    expect(input).toHaveValue('7.5');
    const toggle = screen.getByRole('button', {
      name: 'Свернуть правую область',
    });
    toggle.focus();
    fireEvent.click(toggle);
    expect(input).not.toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Показать правую область' }),
    ).toHaveFocus();
    fireEvent.click(
      screen.getByRole('button', { name: 'Показать правую область' }),
    );
    expect(screen.getByRole('textbox', { name: 'Параметр ряда' })).toBe(input);
    expect(input).toHaveValue('7.5');
  });

  it('does not activate a controlled tab before the task owner accepts it', () => {
    const change = vi.fn();
    const props = slots();
    const { rerender } = render(
      <IDEWorkspaceShell
        {...props}
        rightTab="tool"
        onRightTabChange={change}
      />,
    );
    fireEvent.click(screen.getByRole('tab', { name: 'Свойства' }));
    expect(change).toHaveBeenCalledWith('inspector');
    expect(screen.getByRole('tab', { name: 'Инструмент' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(
      screen.getByRole('textbox', { name: 'Параметр ряда' }),
    ).toBeVisible();
    rerender(
      <IDEWorkspaceShell
        {...props}
        rightTab="inspector"
        onRightTabChange={change}
      />,
    );
    expect(screen.getByRole('textbox', { name: 'Свойство' })).toBeVisible();
  });

  it('shows the tool for an unavailable saved assistant tab without changing its draft', () => {
    render(<IDEWorkspaceShell {...slots()} rightTab="assistant" />);
    expect(
      screen.queryByRole('tab', { name: 'Помощник' }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Инструмент' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(
      screen.getByRole('textbox', { name: 'Параметр ряда' }),
    ).toBeVisible();
    expect(
      screen.queryByRole('textbox', { name: 'Сообщение' }),
    ).not.toBeInTheDocument();
  });

  it('preserves resource and result filters while their surfaces are hidden', () => {
    render(<IDEWorkspaceShell {...slots()} />);
    const search = screen.getByRole('textbox', { name: 'Поиск слоёв' });
    fireEvent.change(search, { target: { value: 'дорога' } });
    fireEvent.click(
      screen.getByRole('button', { name: 'Свернуть ресурсы проекта' }),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Показать ресурсы проекта' }),
    );
    expect(search).toHaveValue('дорога');
    fireEvent.click(screen.getByRole('tab', { name: 'Проверки' }));
    const filter = screen.getByRole('textbox', { name: 'Фильтр проверок' });
    fireEvent.change(filter, { target: { value: 'граница' } });
    fireEvent.click(screen.getByRole('tab', { name: 'Ведомость' }));
    fireEvent.click(
      screen.getByRole('button', { name: 'Свернуть результаты' }),
    );
    fireEvent.click(screen.getByRole('tab', { name: 'Проверки' }));
    expect(screen.getByRole('textbox', { name: 'Фильтр проверок' })).toBe(
      filter,
    );
    expect(filter).toHaveValue('граница');
  });

  it('lets the page open controlled resources on a narrow workspace without losing their draft', () => {
    Object.defineProperty(window, 'innerWidth', {
      configurable: true,
      value: 1024,
    });
    const change = vi.fn();
    const props = slots();
    const { rerender } = render(
      <IDEWorkspaceShell
        {...props}
        resourcesOpen={false}
        onResourcesOpenChange={change}
      />,
    );
    const search = screen.getByLabelText('Поиск слоёв');
    expect(search).not.toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Показать ресурсы проекта' }),
    );
    expect(change).toHaveBeenCalledWith(true);
    expect(search).not.toBeVisible();

    // The accepted page command takes priority over the automatically visible right pane.
    rerender(
      <IDEWorkspaceShell
        {...props}
        resourcesOpen
        onResourcesOpenChange={change}
      />,
    );
    expect(search).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Показать правую область' }),
    ).toBeVisible();
    fireEvent.change(search, { target: { value: 'существующее озеленение' } });
    fireEvent.click(
      screen.getByRole('button', { name: 'Свернуть ресурсы проекта' }),
    );
    expect(change).toHaveBeenLastCalledWith(false);
    expect(search).toBeVisible();
    rerender(
      <IDEWorkspaceShell
        {...props}
        resourcesOpen={false}
        onResourcesOpenChange={change}
      />,
    );
    expect(search).not.toBeVisible();
    rerender(
      <IDEWorkspaceShell
        {...props}
        resourcesOpen
        onResourcesOpenChange={change}
      />,
    );
    expect(screen.getByRole('textbox', { name: 'Поиск слоёв' })).toBe(search);
    expect(search).toHaveValue('существующее озеленение');
  });

  it('uses keyboard resize and an independent preference key', () => {
    vi.useFakeTimers();
    localStorage.setItem('green-editor-layout-v3', '{"width":0.33}');
    render(<IDEWorkspaceShell {...slots()} />);
    const handle = screen.getByRole('separator', {
      name: 'Ширина правой области',
    });
    fireEvent.keyDown(handle, { key: 'ArrowLeft' });
    expect(handle).toHaveAttribute('aria-valuenow', '356');
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(
      JSON.parse(localStorage.getItem(IDE_WORKSPACE_STORAGE_KEY) ?? '{}'),
    ).toMatchObject({ rightWidth: 356 });
    expect(localStorage.getItem('green-editor-layout-v3')).toBe(
      '{"width":0.33}',
    );
    fireEvent.doubleClick(handle);
    expect(handle).toHaveAttribute('aria-valuenow', '340');
  });

  it('uses manual keyboard tab activation so guards run only on activation', () => {
    const change = vi.fn();
    render(
      <IDEWorkspaceShell
        {...slots()}
        rightTab="inspector"
        onRightTabChange={change}
      />,
    );
    screen.getByRole('tab', { name: 'Свойства' }).focus();
    fireEvent.keyDown(screen.getByRole('tablist', { name: 'Правая область' }), {
      key: 'ArrowRight',
    });
    expect(screen.getByRole('tab', { name: 'Инструмент' })).toHaveFocus();
    expect(change).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('tab', { name: 'Инструмент' }));
    expect(change).toHaveBeenCalledWith('tool');
  });
});
