import { createRef, useState } from 'react';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import { SourceReviewLayout } from './SourceReviewLayout';

afterEach(cleanup);

const sections = [
  {
    id: '02',
    label: 'Геометрия',
    title: 'Проверьте геометрию',
    description: 'Контуры и полнота чертежа',
  },
  {
    id: '03',
    label: 'Слои',
    title: 'Проверьте слои',
    description: 'Проверьте назначения слоёв',
  },
  {
    id: '04',
    label: 'Сведения',
    title: 'Паспорт исходных данных',
    description: 'Полнота исходных данных',
  },
];

describe('shared source review layout', () => {
  it('switches sections without remounting a hidden draft or its focused navigation target', async () => {
    const navigation = createRef<HTMLElement>();
    function Review() {
      const [activeSection, setActiveSection] = useState('03');
      return (
        <SourceReviewLayout
          sections={sections}
          activeSection={activeSection}
          onSectionChange={setActiveSection}
          navRef={navigation}
          footer={<button>Сохранить изменения</button>}
        >
          <section hidden={activeSection !== '03'}>
            <input aria-label="Назначение слоя" defaultValue="" />
          </section>
          <section hidden={activeSection !== '04'}>Сведения о файле</section>
        </SourceReviewLayout>
      );
    }
    render(<Review />);
    const draft = screen.getByRole('textbox', { name: 'Назначение слоя' });
    await userEvent.type(draft, 'Дорога');
    const layers = within(screen.getByRole('navigation')).getByRole('button', {
      name: 'Слои',
    });
    await userEvent.click(screen.getByRole('button', { name: 'Сведения' }));
    expect(draft).not.toBeVisible();
    expect(draft).toBeInTheDocument();
    expect(
      screen.getByRole('heading', {
        level: 1,
        name: 'Паспорт исходных данных',
      }),
    ).toBeVisible();
    navigation.current
      ?.querySelector<HTMLButtonElement>('[data-section="03"]')
      ?.focus();
    expect(layers).toHaveFocus();
    await userEvent.keyboard('{Enter}');
    expect(layers).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('textbox')).toBe(draft);
    expect(draft).toHaveValue('Дорога');
  });

  it('keeps recognition actions in the section header and the page action outside the scrolling content', () => {
    render(
      <SourceReviewLayout
        sections={sections}
        activeSection="03"
        onSectionChange={() => undefined}
        headerActions={<button>Распознавание слоёв</button>}
        headerProgress={
          <span role="progressbar" aria-label="Читаем сведения" />
        }
        status={<span role="status">Изменения ещё не применены</span>}
        footer={<button>Изменить назначения</button>}
      >
        <p>Таблица слоёв</p>
      </SourceReviewLayout>,
    );
    const header = screen.getByRole('heading', { level: 1 }).closest('header');
    expect(
      screen
        .getByRole('button', { name: 'Распознавание слоёв' })
        .closest('header'),
    ).toBe(header);
    expect(screen.getByRole('progressbar').closest('header')).toBe(header);
    expect(screen.getByRole('status')).toBeVisible();
    const footer = screen
      .getByRole('button', { name: 'Изменить назначения' })
      .closest('footer');
    expect(footer).not.toBeNull();
    expect(footer?.previousElementSibling).toContainElement(
      screen.getByText('Таблица слоёв'),
    );
  });
});
