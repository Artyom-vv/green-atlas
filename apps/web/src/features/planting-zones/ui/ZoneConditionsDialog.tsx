import { useId } from 'react';
import { useForm } from 'react-hook-form';
import type {
  PlantingZoneAssignment,
  TerritoryContext,
  SiteConditions,
} from '@green/api-client';
import {
  Button,
  Checkbox,
  Dialog,
  Disclosure,
  Field,
  FormActions,
  InlineMessage,
  Select,
  TextInput,
} from '@green/ui';
import { territoryLabels } from '@/entities/species/model/assortmentLabels';

type Values = {
  category: TerritoryContext['category'] | '';
  regime: TerritoryContext['regime'] | '';
  basis: string;
  spread: boolean;
  light: NonNullable<SiteConditions['light']> | '';
  moisture: NonNullable<SiteConditions['moisture']> | '';
  drainage: NonNullable<SiteConditions['drainage']> | '';
  observation: string;
};

export function ZoneConditionsDialog({
  zone,
  saving,
  error,
  onSave,
  onClose,
}: {
  zone: PlantingZoneAssignment;
  saving: boolean;
  error?: string;
  onSave: (zone: PlantingZoneAssignment) => void;
  onClose: () => void;
}) {
  const id = useId();
  const form = useForm<Values>({
    defaultValues: {
      category: zone.territory?.category ?? '',
      regime: zone.territory?.regime ?? '',
      basis: zone.territory?.basis ?? '',
      spread: zone.territory?.spread_control_confirmed ?? false,
      light: zone.site_conditions?.light ?? '',
      moisture: zone.site_conditions?.moisture ?? '',
      drainage: zone.site_conditions?.drainage ?? '',
      observation: zone.site_conditions?.basis ?? '',
    },
  });
  const observed = Boolean(
    form.watch('light') || form.watch('moisture') || form.watch('drainage'),
  );
  return (
    <Dialog
      open
      title={`Условия участка: ${zone.label}`}
      onClose={() => {
        if (!saving) onClose();
      }}
      footer={
        <FormActions>
          <Button variant="secondary" disabled={saving} onClick={onClose}>
            К участкам
          </Button>
          <Button variant="primary" type="submit" form={id} loading={saving}>
            Сохранить условия
          </Button>
        </FormActions>
      }
    >
      <form
        id={id}
        className="grid gap-5"
        onSubmit={form.handleSubmit((v) => {
          if (
            saving ||
            !v.category ||
            !v.regime ||
            !v.basis.trim() ||
            (observed && v.observation.trim().length < 3)
          )
            return;
          onSave({
            ...zone,
            territory: {
              category: v.category,
              regime: v.regime,
              basis: v.basis.trim(),
              spread_control_confirmed: v.spread,
            },
            site_conditions: observed
              ? {
                  light: v.light || null,
                  moisture: v.moisture || null,
                  drainage: v.drainage || null,
                  basis: v.observation.trim(),
                }
              : null,
          });
        })}
      >
        <p className="m-0 text-xs leading-5 text-neutral-600">
          {zone.territory
            ? 'Загружены сохранённые сведения участка.'
            : 'Категория территории в данных участка не задана. Уточните её по проекту благоустройства.'}
        </p>
        <Field
          label="Категория территории"
          hint="Определяет допустимые виды растений."
        >
          <Select required disabled={saving} {...form.register('category')}>
            <option value="" disabled>
              Выберите категорию
            </option>
            {Object.entries(territoryLabels).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </Select>
        </Field>
        <Field
          label="Режим территории"
          hint="По проектной документации. Если сведений нет, выберите «Неизвестен»."
        >
          <Select required disabled={saving} {...form.register('regime')}>
            <option value="" disabled>
              Уточните режим
            </option>
            <option value="ordinary">Обычная городская территория</option>
            <option value="individual_project">
              Особый режим — индивидуальный проект
            </option>
            <option value="unknown">Неизвестен</option>
          </Select>
        </Field>
        <Field
          label="Основание выбора"
          hint="Документ или раздел, из которого взята категория."
        >
          <TextInput
            required
            disabled={saving}
            maxLength={500}
            placeholder="Проект благоустройства, раздел или обследование"
            {...form.register('basis')}
          />
        </Field>
        <Checkbox
          disabled={saving}
          label="Для видов с порослью предусмотрен контроль распространения"
          {...form.register('spread')}
        />
        <Disclosure title="Свет и почва" defaultOpen={observed} variant="plain">
          <fieldset disabled={saving} className="m-0 grid gap-3 border-0 p-0">
            <p className="m-0 text-xs leading-5 text-neutral-600">
              Необязательно. Укажите только известные данные из обследования
              участка.
            </p>
            <Field label="Свет">
              <Select {...form.register('light')}>
                <option value="">Нет данных</option>
                <option value="full_sun">Солнце</option>
                <option value="partial_shade">Полутень</option>
                <option value="full_shade">Тень</option>
              </Select>
            </Field>
            <Field label="Влажность почвы">
              <Select {...form.register('moisture')}>
                <option value="">Нет данных</option>
                <option value="moist">Умеренно влажная</option>
                <option value="occasionally_dry">Периодически сухая</option>
                <option value="occasionally_wet">
                  Периодически переувлажнённая
                </option>
                <option value="persistently_wet">
                  Постоянно переувлажнённая
                </option>
              </Select>
            </Field>
            <Field label="Дренаж">
              <Select {...form.register('drainage')}>
                <option value="">Нет данных</option>
                <option value="well_drained">Хороший</option>
                <option value="poorly_drained">Плохой</option>
              </Select>
            </Field>
            {observed && (
              <Field label="Источник наблюдений">
                <TextInput
                  required
                  minLength={3}
                  maxLength={1000}
                  {...form.register('observation')}
                />
              </Field>
            )}
          </fieldset>
        </Disclosure>
        {error && <InlineMessage tone="error">{error}</InlineMessage>}
      </form>
    </Dialog>
  );
}
