import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { useState, type ComponentProps } from 'react';
import { useForm } from 'react-hook-form';
import type { Plan, ReleasePackage } from '@green/api-client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ReleasePanel as ControlledReleasePanel } from './ReleasePanel';
import type { ReleaseFormValues } from '../model/releaseForm';

const initialForm = (sceneHorizon = 0): ReleaseFormValues => ({
  mode: 'draft',
  sceneHorizon,
  basis: {
    pp616_status: 'pending',
    pp616_reference: '',
    pp1160_status: 'pending',
    pp1160_reference: '',
    confirmed_by: '',
  },
});
type TestProps = Omit<
  ComponentProps<typeof ControlledReleasePanel>,
  | 'formMethods'
  | 'formOpen'
  | 'onOpenForm'
  | 'onShowFiles'
  | 'onClearDraft'
  | 'onReviewContext'
> & { growthHorizon?: number };
// This UI harness supplies controlled values; persistence and submission belong to feature tests.
function ReleasePanel(props: TestProps) {
  const formMethods = useForm<ReleaseFormValues>({
    defaultValues: initialForm(props.growthHorizon),
  });
  const [editing, setEditing] = useState<string>();
  return (
    <ControlledReleasePanel
      {...props}
      formMethods={formMethods}
      formOpen={!props.release || editing === props.release.id}
      onOpenForm={() => setEditing(props.release?.id)}
      onShowFiles={() => setEditing(undefined)}
      onClearDraft={() => formMethods.reset(initialForm(props.growthHorizon))}
      onReviewContext={vi.fn()}
    />
  );
}

afterEach(cleanup);

const draftPlan = {
  id: 'plan-1',
  version: 3,
  issues: [],
  objects: [
    {
      id: 'tree-1',
      kind: 'tree',
      x: 10,
      y: 20,
      radius: 1.6,
      size_class: 'unspecified',
      species_revision_id: null,
      spacing_policy: 'balanced',
      locked: false,
      status: 'valid',
    },
  ],
} as Plan;

describe('ReleasePanel', () => {
  it('exposes forecast without opening another section and sends the chosen year', () => {
    const onCreate = vi.fn();
    const onGrowthHorizon = vi.fn();
    render(
      <ReleasePanel
        plan={draftPlan}
        onCreate={onCreate}
        onDownload={vi.fn()}
        onGrowthHorizon={onGrowthHorizon}
      />,
    );
    const slider = screen.getByRole('slider', {
      name: 'Горизонт прогноза в пакете',
    });
    expect(slider).toBeVisible();
    fireEvent.change(slider, { target: { value: '12' } });
    expect(onGrowthHorizon).toHaveBeenCalledWith(12);
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать черновой пакет' }),
    );
    expect(onCreate).toHaveBeenCalledWith({ mode: 'draft', scene_horizon: 12 });
  });

  it('shows unverified source constraints and keeps the draft available', () => {
    const plan = {
      ...draftPlan,
      issues: [
        {
          code: 'SOURCE_REVIEW_PENDING',
          severity: 'warning',
          title: 'Source requires review',
          description: 'Constraints have not been calculated',
        },
      ],
    } as Plan;
    render(
      <ReleasePanel plan={plan} onCreate={vi.fn()} onDownload={vi.fn()} />,
    );
    expect(screen.getByText('Ограничения исходных данных')).toBeVisible();
    expect(screen.getByText('Не проверены')).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Собрать черновой пакет' }),
    ).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Финальный' }));
    expect(
      screen.getByText(
        'Проверьте исходные данные и выполните расчёт ограничений.',
      ),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Собрать финальный пакет' }),
    ).toBeDisabled();
  });

  it('allows an honest draft while blocking a final package without species', () => {
    const onCreate = vi.fn();
    render(
      <ReleasePanel
        plan={draftPlan}
        onCreate={onCreate}
        onDownload={vi.fn()}
      />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать черновой пакет' }),
    );
    expect(onCreate).toHaveBeenCalledWith({ mode: 'draft', scene_horizon: 0 });
    expect(
      screen.queryByLabelText('Решение по ПП-616'),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Финальный' }));
    expect(
      screen.getByRole('button', { name: 'Собрать финальный пакет' }),
    ).toBeDisabled();
    expect(screen.getByText(/не является согласованием/)).toBeVisible();
  });

  it('requires attributable PP-616 and PP-1160 decisions before a final package', () => {
    const onCreate = vi.fn();
    const assignedPlan = {
      ...draftPlan,
      objects: [{ ...draftPlan.objects![0], species_revision_id: 'tilia@1' }],
    } as Plan;
    render(
      <ReleasePanel
        plan={assignedPlan}
        onCreate={onCreate}
        onDownload={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Финальный' }));

    const finalButton = screen.getByRole('button', {
      name: 'Собрать финальный пакет',
    });
    expect(finalButton).toBeDisabled();
    expect(finalButton).toHaveAccessibleDescription(
      'Заполните оба решения, их основания и ответственного за проверку.',
    );
    expect(
      within(
        screen.getByRole('group', { name: 'Решение и основание ПП-616' }),
      ).getByLabelText('Основание решения по ПП-616'),
    ).toBeVisible();
    fireEvent.change(screen.getByLabelText('Решение по ПП-616'), {
      target: { value: 'not_applicable' },
    });
    fireEvent.change(screen.getByLabelText('Основание решения по ПП-616'), {
      target: { value: 'Новые посадки без удаления' },
    });
    fireEvent.change(screen.getByLabelText('Решение по ПП-1160'), {
      target: { value: 'not_required' },
    });
    fireEvent.change(screen.getByLabelText('Основание решения по ПП-1160'), {
      target: { value: 'Удаление не предусмотрено' },
    });
    fireEvent.change(screen.getByLabelText('Ответственный за проверку'), {
      target: { value: 'Иванов И И' },
    });

    expect(finalButton).toBeEnabled();
    expect(finalButton).not.toHaveAttribute('aria-describedby');
    fireEvent.click(finalButton);
    expect(onCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        mode: 'final',
        regulatory_basis: {
          pp616_status: 'not_applicable',
          pp616_reference: 'Новые посадки без удаления',
          pp1160_status: 'not_required',
          pp1160_reference: 'Удаление не предусмотрено',
          confirmed_by: 'Иванов И И',
        },
      }),
    );
  });

  it('downloads the bundle and exposes its individual files', () => {
    const onDownload = vi.fn();
    const release = {
      id: 'release-1',
      project_id: 'project-1',
      plan_version: 3,
      geometry_version: 1,
      mode: 'draft',
      status: 'draft',
      created_at: '2026-08-28T00:00:00Z',
      rule_set_revision: 'rules-1',
      species_catalog_revision: 'catalog-1',
      scene_horizon: 20,
      warnings: [],
      artifacts: [
        {
          id: 'bundle',
          filename: 'release.zip',
          kind: 'bundle',
          media_type: 'application/zip',
          size: 100,
          sha256: 'a',
          download_url: '/bundle',
        },
        {
          id: 'schedule',
          filename: 'schedule.csv',
          kind: 'schedule',
          media_type: 'text/csv',
          size: 20,
          sha256: 'b',
          download_url: '/schedule',
        },
      ],
    } as ReleasePackage;
    render(
      <ReleasePanel
        plan={draftPlan}
        release={release}
        onCreate={vi.fn()}
        onDownload={onDownload}
      />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Скачать полный пакет' }),
    );
    fireEvent.click(screen.getByText('Отдельные файлы', { exact: true }));
    fireEvent.click(
      screen.getByRole('button', { name: /Посадочная ведомость/ }),
    );
    expect(onDownload.mock.calls).toEqual([['/bundle'], ['/schedule']]);
  });

  it('labels an older package and can build again after the plan changes', () => {
    const onCreate = vi.fn();
    const release = {
      id: 'old',
      plan_version: 2,
      mode: 'draft',
      scene_horizon: 0,
      artifacts: [],
    } as unknown as ReleasePackage;
    const view = render(
      <ReleasePanel
        plan={draftPlan}
        release={release}
        onCreate={onCreate}
        onDownload={vi.fn()}
      />,
    );
    expect(screen.getByText(/Этот пакет содержит версию 2/)).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать новый пакет' }),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать черновой пакет' }),
    );
    expect(onCreate).toHaveBeenCalledWith({ mode: 'draft', scene_horizon: 0 });
    view.rerender(
      <ReleasePanel
        plan={draftPlan}
        release={{ ...release, id: 'new', plan_version: 3 }}
        onCreate={onCreate}
        onDownload={vi.fn()}
      />,
    );
    expect(screen.getByText('Черновой пакет готов')).toBeVisible();
    expect(
      screen.queryByText(/Этот пакет содержит версию/),
    ).not.toBeInTheDocument();
  });

  it('marks a package stale after a zone change even when the planting plan version is unchanged', () => {
    const onCreate = vi.fn();
    const onDownload = vi.fn();
    const release: ReleasePackage = {
      id: 'before-zone-edit',
      project_id: 'project-1',
      plan_version: draftPlan.version!,
      geometry_version: 1,
      mode: 'draft',
      status: 'draft',
      created_at: '2026-09-10T00:00:00Z',
      rule_set_revision: 'rules-1',
      species_catalog_revision: 'catalog-1',
      scene_horizon: 0,
      warnings: [],
      artifacts: [
        {
          id: 'bundle',
          filename: 'release.zip',
          kind: 'bundle',
          media_type: 'application/zip',
          size: 100,
          sha256: 'checksum',
          download_url: '/saved-bundle',
        },
      ],
    };
    const view = render(
      <ReleasePanel
        plan={draftPlan}
        geometryVersion={1}
        release={release}
        onCreate={onCreate}
        onDownload={onDownload}
      />,
    );
    expect(screen.queryByText(/изменились участки/)).not.toBeInTheDocument();
    view.rerender(
      <ReleasePanel
        plan={draftPlan}
        geometryVersion={2}
        release={release}
        onCreate={onCreate}
        onDownload={onDownload}
      />,
    );
    expect(
      screen.getByText(/изменились участки или исходная геометрия/),
    ).toBeVisible();
    expect(
      screen.queryByText(/Этот пакет содержит версию/),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Скачать полный пакет' }),
    );
    expect(onDownload).toHaveBeenCalledWith('/saved-bundle');
    expect(onCreate).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать новый пакет' }),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать черновой пакет' }),
    );
    expect(onCreate).toHaveBeenCalledWith({ mode: 'draft', scene_horizon: 0 });
    view.rerender(
      <ReleasePanel
        plan={draftPlan}
        geometryVersion={2}
        release={{ ...release, id: 'after-zone-edit', geometry_version: 2 }}
        onCreate={onCreate}
        onDownload={onDownload}
      />,
    );
    expect(screen.getByText('Черновой пакет готов')).toBeVisible();
    expect(screen.queryByText(/изменились участки/)).not.toBeInTheDocument();
  });

  it('explains only the remaining blockers and keeps draft creation independent of final criteria', () => {
    const onCreate = vi.fn();
    const plan = {
      ...draftPlan,
      issues: [
        {
          severity: 'error',
          code: 'DISTANCE',
          title: 'Отступ',
          description: 'Недостаточный отступ',
        },
      ],
    } as Plan;
    const view = render(
      <ReleasePanel plan={plan} onCreate={onCreate} onDownload={vi.fn()} />,
    );
    expect(
      screen.getByRole('button', { name: 'Собрать черновой пакет' }),
    ).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Финальный' }));
    const final = screen.getByRole('button', {
      name: 'Собрать финальный пакет',
    });
    expect(final).toBeDisabled();
    expect(final).toHaveAccessibleDescription(
      'Устраните ошибки размещения. Назначьте виды посадкам. Заполните оба решения, их основания и ответственного за проверку.',
    );
    fireEvent.click(final);
    expect(onCreate).not.toHaveBeenCalled();
    view.rerender(
      <ReleasePanel
        plan={{ ...draftPlan, objects: [] }}
        onCreate={onCreate}
        onDownload={vi.fn()}
      />,
    );
    expect(
      screen.queryByText('Устраните ошибки размещения.'),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText('Назначьте виды посадкам.'),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Черновой' }));
    expect(
      screen.getByRole('button', { name: 'Собрать черновой пакет' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Собрать черновой пакет' }),
    ).toHaveAccessibleDescription('Добавьте посадки в план перед выпуском.');
  });

  it('freezes the selected parameters while building and preserves them after a failure', () => {
    const onCreate = vi.fn(),
      onGrowthHorizon = vi.fn(),
      onDownload = vi.fn();
    const assignedPlan = {
      ...draftPlan,
      objects: [{ ...draftPlan.objects![0], species_revision_id: 'tilia@1' }],
    } as Plan;
    const props = {
      plan: assignedPlan,
      onCreate,
      onGrowthHorizon,
      onDownload,
      growthHorizon: 20,
    };
    const view = render(<ReleasePanel {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Финальный' }));
    fireEvent.click(screen.getByText('Прогноз в пакете', { exact: true }));
    fireEvent.change(screen.getByLabelText('Решение по ПП-616'), {
      target: { value: 'documented' },
    });
    fireEvent.change(screen.getByLabelText('Основание решения по ПП-616'), {
      target: { value: 'Документ 616' },
    });
    fireEvent.change(screen.getByLabelText('Решение по ПП-1160'), {
      target: { value: 'documented' },
    });
    fireEvent.change(screen.getByLabelText('Основание решения по ПП-1160'), {
      target: { value: 'Документ 1160' },
    });
    fireEvent.change(screen.getByLabelText('Ответственный за проверку'), {
      target: { value: 'Иванов' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать финальный пакет' }),
    );
    expect(onCreate).toHaveBeenCalledOnce();
    expect(onCreate).toHaveBeenCalledWith(
      expect.objectContaining({ mode: 'final', scene_horizon: 20 }),
    );

    view.rerender(<ReleasePanel {...props} loading />);
    for (const control of [
      ...screen.getAllByRole('combobox'),
      ...screen.getAllByRole('textbox'),
      screen.getByRole('slider'),
      ...screen.getAllByRole('button'),
    ]) {
      expect(control).toBeDisabled();
    }
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать финальный пакет' }),
    );
    expect(onCreate).toHaveBeenCalledOnce();

    view.rerender(
      <ReleasePanel
        {...props}
        error="Не удалось собрать пакет. Повторите попытку."
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Не удалось собрать пакет. Повторите попытку.',
    );
    expect(screen.getByLabelText('Основание решения по ПП-616')).toHaveValue(
      'Документ 616',
    );
    expect(screen.getByLabelText('Основание решения по ПП-1160')).toHaveValue(
      'Документ 1160',
    );
    expect(screen.getByLabelText('Ответственный за проверку')).toHaveValue(
      'Иванов',
    );
    expect(screen.getByRole('slider')).toBeEnabled();
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать финальный пакет' }),
    );
    expect(onCreate.mock.calls[1]).toEqual(onCreate.mock.calls[0]);
  });

  it('can return to the immutable saved files without creating another package', () => {
    const onCreate = vi.fn(),
      onDownload = vi.fn();
    const release = {
      id: 'saved',
      plan_version: 2,
      geometry_version: 1,
      mode: 'draft',
      scene_horizon: 20,
      artifacts: [
        {
          id: 'bundle',
          kind: 'bundle',
          size: 2048,
          download_url: '/saved.zip',
        },
      ],
    } as ReleasePackage;
    render(
      <ReleasePanel
        plan={draftPlan}
        geometryVersion={2}
        release={release}
        growthHorizon={40}
        onGrowthHorizon={vi.fn()}
        onCreate={onCreate}
        onDownload={onDownload}
      />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать новый пакет' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Финальный' }));
    fireEvent.click(
      screen.getByRole('button', { name: 'Вернуться к файлам пакета' }),
    );
    expect(screen.getByText('Черновой пакет готов')).toBeVisible();
    expect(screen.getByText('Прогноз в пакете: 20 лет')).toBeVisible();
    expect(screen.getByText(/Этот пакет содержит версию 2/)).toBeVisible();
    expect(
      screen.getByText(/изменились участки или исходная геометрия/),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Скачать полный пакет' }),
    );
    expect(onDownload).toHaveBeenCalledWith('/saved.zip');
    expect(onCreate).not.toHaveBeenCalled();
  });
});
