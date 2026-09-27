import type { Meta, StoryObj } from '@storybook/react';
import { MousePointer2, PenLine, Plus, Search } from 'lucide-react';
import { useState, type FC } from 'react';
import {
  Button,
  Checkbox,
  Combobox,
  ControlProvider,
  Dialog,
  Field,
  FieldGrid,
  FieldGroup,
  FileDropzone,
  FormActions,
  Icon,
  NumberInput,
  Panel,
  PanelHeader,
  Select,
  Surface,
  Text,
  TextInput,
} from '../index';

const meta: Meta = { title: 'Green Atlas/Composition' };
export default meta;
type Story = StoryObj;

interface FormExampleProps {
  width: number;
}
const FormExample: FC<FormExampleProps> = ({ width }) => {
  const [count, setCount] = useState(40);
  const [species, setSpecies] = useState('lime');
  const [open, setOpen] = useState(false);
  return (
    <ControlProvider size="compact">
      <div style={{ width }} className="max-w-full">
        <Panel
          title="Параметры задачи"
          description="Целые контролы переходят на следующую строку"
        >
          <div className="grid min-w-0 gap-4">
            <FieldGroup legend="Состав">
              <Field label="Порода">
                <Combobox
                  options={[
                    {
                      value: 'lime',
                      label: 'Липа мелколистная',
                      description: 'Tilia cordata',
                    },
                    {
                      value: 'oak',
                      label: 'Дуб черешчатый',
                      description: 'Quercus robur',
                    },
                  ]}
                  value={species}
                  onChange={setSpecies}
                />
              </Field>
            </FieldGroup>
            <FieldGrid>
              <Field label="Количество">
                <NumberInput
                  value={count}
                  onValueChange={setCount}
                  min={1}
                  max={5000}
                />
              </Field>
              <Field label="Плотность">
                <Select>
                  <option>Естественно</option>
                  <option>Свободно</option>
                </Select>
              </Field>
            </FieldGrid>
            <Checkbox label="Сохранять существующие посадки на выбранной территории" />
            <Field
              label="Комментарий"
              hint="Черновик остаётся после закрытия каталога"
            >
              <TextInput defaultValue="Новая посадка" />
            </Field>
            <FormActions layout="equal" minItemWidth="13rem">
              <Button
                startIcon={<Search size={32} color="red" />}
                onClick={() => setOpen(true)}
              >
                Открыть каталог
              </Button>
              <Button variant="primary" startIcon={<Plus />}>
                Показать предпросмотр
              </Button>
            </FormActions>
          </div>
        </Panel>
        <Dialog
          open={open}
          onClose={() => setOpen(false)}
          title="Выбор из каталога"
          size="form"
          footer={
            <Button onClick={() => setOpen(false)}>Вернуться к задаче</Button>
          }
        >
          <Field label="Найти породу">
            <Combobox
              aria-label="Найти породу"
              options={[
                { value: 'lime', label: 'Липа' },
                { value: 'oak', label: 'Дуб' },
              ]}
              value={species}
              onChange={setSpecies}
            />
          </Field>
        </Dialog>
      </div>
    </ControlProvider>
  );
};

export const Panel300: Story = { render: () => <FormExample width={300} /> };
export const Panel340: Story = { render: () => <FormExample width={340} /> };
export const Panel480: Story = { render: () => <FormExample width={480} /> };
export const SlotsAndTypography: Story = {
  render: () => (
    <div className="grid max-w-2xl gap-4 p-6">
      <Text as="h1" variant="pageHeading">
        Рабочий проект
      </Text>
      <Text>Обычный текст</Text>
      <Text variant="caption">Пояснение к действию</Text>
      <Icon icon={<Plus />} label="Добавить" size={24} />
      <Surface title="Стандартный заголовок">
        <div className="p-4">Содержимое с собственными отступами.</div>
      </Surface>
      <Panel
        title="Не отображается"
        header={
          <PanelHeader>
            <Text as="h2" variant="heading">
              Своя композиция
            </Text>
            <Button controlSize="compact">Действие</Button>
          </PanelHeader>
        }
      >
        Стандартная типографика и отступы предоставляются готовой частью.
      </Panel>
    </div>
  ),
};
export const WholeControlWrapping: Story = {
  render: () => (
    <ControlProvider size="compact">
      <div className="grid w-[300px] gap-4 p-3">
        <div className="flex flex-wrap gap-2">
          <Button startIcon={<Plus />}>Нарисовать линию</Button>
          <Button>Выбрать ось</Button>
          <Button>Сбросить</Button>
        </div>
        <Button
          className="grid h-auto grid-cols-[40px_1fr] gap-3 text-left"
          content={
            <>
              <span className="rounded-card size-10 bg-green-100" />
              <span className="grid min-w-0 gap-1">
                <Text variant="label">Липа мелколистная</Text>
                <Text variant="caption">Дополнительное описание карточки</Text>
              </span>
            </>
          }
        />
      </div>
    </ControlProvider>
  ),
};
export const EqualActionsAtBoundary: Story = {
  render: () => (
    <ControlProvider size="compact">
      <div className="grid gap-6">
        {[300, 340, 480].map((width) => (
          <div key={width} style={{ width }} className="grid gap-3 p-3">
            <Text variant="caption">Панель {width} px</Text>
            <FormActions layout="equal" minItemWidth="10rem">
              <Button startIcon={<MousePointer2 />}>Выбрать в DXF</Button>
              <Button startIcon={<PenLine />}>Нарисовать линию</Button>
            </FormActions>
            <FormActions layout="equal">
              <Button>Отмена</Button>
              <Button variant="primary">Проверить места</Button>
            </FormActions>
          </div>
        ))}
      </div>
    </ControlProvider>
  ),
};
export const FileSelection: Story = {
  render: () => (
    <div className="max-w-lg p-6">
      <FileDropzone
        onFilesAccepted={() => undefined}
        message="Форматы задаёт вызывающий сценарий."
      />
    </div>
  ),
};
