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
  Field,
  FormActions,
  ScrollArea,
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
  onSave,
  onClose,
}: {
  zone: PlantingZoneAssignment;
  saving: boolean;
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
      title={`Условия участка · ${zone.label}`}
      onClose={onClose}
      footer={
        <FormActions>
          <Button variant="secondary" onClick={onClose}>
            Отмена
          </Button>
          <Button variant="primary" type="submit" form={id} loading={saving}>
            Сохранить условия
          </Button>
        </FormActions>
      }
    >
      <ScrollArea className="max-h-[65vh]" contentClassName="p-1">
        <form
          id={id}
          className="grid gap-5"
          onSubmit={form.handleSubmit((v) => {
            if (
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
          <Field label="Категория территории">
            <Select required {...form.register('category')}>
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
          <Field label="Режим территории">
            <Select required {...form.register('regime')}>
              <option value="" disabled>
                Уточните режим
              </option>
              <option value="ordinary">Обычная городская территория</option>
              <option value="individual_project">
                Особый режим — индивидуальный проект
              </option>
              <option value="unknown">Режим пока неизвестен</option>
            </Select>
          </Field>
          <Field label="Основание выбора">
            <TextInput
              required
              maxLength={500}
              placeholder="Проект благоустройства, раздел или обследование"
              {...form.register('basis')}
            />
          </Field>
          <Checkbox
            label="Для видов с порослью предусмотрен контроль распространения"
            {...form.register('spread')}
          />
          <fieldset className="m-0 grid gap-3 border-0 border-t border-neutral-200 p-0 pt-4">
            <legend className="px-0 text-sm font-medium">
              Известные условия места
            </legend>
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
          <p className="m-0 text-xs leading-5 text-neutral-600">
            Условия сохраняются для этого участка. После изменения существующие
            посадки будут проверены повторно.
          </p>
        </form>
      </ScrollArea>
    </Dialog>
  );
}
