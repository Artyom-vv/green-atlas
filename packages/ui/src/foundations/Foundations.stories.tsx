import type { Meta, StoryObj } from '@storybook/react';
import { AlertTriangle, MousePointer2, Plus, Trash2 } from 'lucide-react';
import {
  Button,
  Checkbox,
  Combobox,
  DataTable,
  FormField,
  IconButton,
  Inline,
  InlineMessage,
  NumberInput,
  Panel,
  Progress,
  Select,
  Stack,
  TextArea,
  TextInput,
  Toolbar,
  type ButtonVariant,
  type ControlSize,
} from '../index';

const meta: Meta = { title: 'Technical Atlas/Foundations' };
export default meta;
type Story = StoryObj;

export const Controls: Story = {
  render: () => (
    <div style={{ padding: 32, maxWidth: 1120 }}>
      <Stack gap={6}>
        <Inline gap={3}>
          <Button variant="primary">Основное действие</Button>
          <Button>Вторичное</Button>
          <Button variant="ghost">Прозрачное</Button>
          <Button variant="danger" icon={Trash2}>
            Удалить
          </Button>
          <Button loading>Обработка</Button>
          <Button disabled>Недоступно</Button>
        </Inline>
        <Toolbar>
          <IconButton icon={MousePointer2} label="Выбрать" active />
          <IconButton icon={Plus} label="Добавить" />
          <IconButton icon={Trash2} label="Удалить" variant="danger" />
        </Toolbar>
        <Panel
          title="Поля и состояния"
          description="Базовые контролы без предметных терминов"
        >
          <Stack gap={4}>
            <Inline gap={4}>
              <FormField label="Текст" error="Поле заполнено неверно">
                <TextInput aria-invalid="true" defaultValue="Ошибка" />
              </FormField>
              <FormField label="Число">
                <NumberInput defaultValue={3} unit="м" />
              </FormField>
              <FormField label="Выбор">
                <Select defaultValue="a">
                  <option value="a">Вариант A</option>
                  <option value="b">Вариант B</option>
                </Select>
              </FormField>
            </Inline>
            <Checkbox label="Параметр включён" defaultChecked />
            <FormField label="Описание">
              <TextArea placeholder="Введите описание" />
            </FormField>
            <Progress label="Обработка" value={64} />
            <InlineMessage tone="warning" title="Требуется проверка">
              <span>Некоторые данные необходимо подтвердить.</span>
            </InlineMessage>
          </Stack>
        </Panel>
        <DataTable>
          <thead>
            <tr>
              <th>Название</th>
              <th>Статус</th>
              <th>Действие</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Пример строки</td>
              <td>Готово</td>
              <td>
                <Button variant="ghost" icon={AlertTriangle}>
                  Открыть
                </Button>
              </td>
            </tr>
          </tbody>
        </DataTable>
      </Stack>
    </div>
  ),
};

const controlSizes: ControlSize[] = ['compact', 'default', 'large'];
const buttonVariants: ButtonVariant[] = [
  'primary',
  'secondary',
  'ghost',
  'danger',
];

export const ButtonMatrix: Story = {
  render: () => (
    <div style={{ padding: 32, maxWidth: 1120 }}>
      <Stack gap={6}>
        <header>
          <h2 style={{ margin: 0, fontSize: 20 }}>Кнопки</h2>
          <p style={{ color: 'var(--ink-500)' }}>
            Все размеры, типы и обязательные состояния из одного контракта.
          </p>
        </header>
        <DataTable>
          <thead>
            <tr>
              <th>Размер</th>
              {buttonVariants.map((variant) => (
                <th key={variant}>{variant}</th>
              ))}
              <th>disabled</th>
              <th>loading</th>
            </tr>
          </thead>
          <tbody>
            {controlSizes.map((size) => (
              <tr key={size}>
                <td>
                  <code>{size}</code>
                </td>
                {buttonVariants.map((variant) => (
                  <td key={variant}>
                    <Button variant={variant} controlSize={size} icon={Plus}>
                      Действие
                    </Button>
                  </td>
                ))}
                <td>
                  <Button controlSize={size} disabled>
                    Недоступно
                  </Button>
                </td>
                <td>
                  <Button controlSize={size} loading>
                    Загрузка
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </DataTable>
        <DataTable>
          <thead>
            <tr>
              <th>Размер IconButton</th>
              <th>default</th>
              <th>selected</th>
              <th>danger</th>
              <th>disabled</th>
            </tr>
          </thead>
          <tbody>
            {controlSizes.map((size) => (
              <tr key={size}>
                <td>
                  <code>{size}</code>
                </td>
                <td>
                  <IconButton
                    icon={MousePointer2}
                    label={`Выбрать, ${size}`}
                    controlSize={size}
                  />
                </td>
                <td>
                  <IconButton
                    icon={MousePointer2}
                    label={`Выбрано, ${size}`}
                    controlSize={size}
                    active
                  />
                </td>
                <td>
                  <IconButton
                    icon={Trash2}
                    label={`Удалить, ${size}`}
                    controlSize={size}
                    variant="danger"
                  />
                </td>
                <td>
                  <IconButton
                    icon={Plus}
                    label={`Недоступно, ${size}`}
                    controlSize={size}
                    disabled
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      </Stack>
    </div>
  ),
};

export const FieldMatrix: Story = {
  render: () => (
    <div style={{ padding: 32, maxWidth: 960 }}>
      <Stack gap={6}>
        <header>
          <h2 style={{ margin: 0, fontSize: 20 }}>Поля</h2>
          <p style={{ color: 'var(--ink-500)' }}>
            Высота меняется, внутренний ритм и семантические состояния
            сохраняются.
          </p>
        </header>
        <DataTable>
          <thead>
            <tr>
              <th>Размер</th>
              <th>TextInput</th>
              <th>NumberInput</th>
              <th>Select</th>
              <th>Combobox</th>
            </tr>
          </thead>
          <tbody>
            {controlSizes.map((size) => (
              <tr key={size}>
                <td>
                  <code>{size}</code>
                </td>
                <td>
                  <TextInput
                    controlSize={size}
                    defaultValue="Значение"
                    aria-label={`Текст, ${size}`}
                  />
                </td>
                <td>
                  <NumberInput
                    controlSize={size}
                    defaultValue={3}
                    unit="м"
                    aria-label={`Число, ${size}`}
                  />
                </td>
                <td>
                  <Select
                    controlSize={size}
                    defaultValue="a"
                    aria-label={`Выбор, ${size}`}
                  >
                    <option value="a">Вариант A</option>
                  </Select>
                </td>
                <td>
                  <Combobox
                    controlSize={size}
                    value="a"
                    options={[
                      {
                        value: 'a',
                        label: 'Вариант A',
                        description: 'Описание',
                      },
                      { value: 'b', label: 'Вариант B' },
                    ]}
                    onChange={() => undefined}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      </Stack>
    </div>
  ),
};
