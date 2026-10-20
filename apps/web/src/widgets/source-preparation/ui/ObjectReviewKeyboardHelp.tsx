import {
  Icon,
  IconButton,
  Popover,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
} from '@green/ui';
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Keyboard,
} from 'lucide-react';
import type { ReactNode } from 'react';

function Key({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded-control inline-flex min-h-6 min-w-6 items-center justify-center border border-solid border-neutral-300 bg-white px-1 font-mono text-xs leading-4 text-neutral-700">
      {children}
    </kbd>
  );
}

/** Keyboard instructions are secondary; queue and viewport keep their handlers. */
export function ObjectReviewKeyboardHelp() {
  return (
    <Popover>
      <PopoverTrigger
        render={
          <IconButton
            variant="ghost"
            controlSize="default"
            icon={<Keyboard />}
            label="Управление с клавиатуры"
          />
        }
      />
      <PopoverPopup size="default" align="end">
        <PopoverTitle className="m-0 text-sm font-medium">
          Управление с клавиатуры
        </PopoverTitle>
        <dl className="m-0 mt-3 grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-4 gap-y-2 text-sm leading-5">
          <dt className="flex gap-1">
            <Key>
              <Icon icon={<ArrowUp />} label="Стрелка вверх" />
            </Key>
            <Key>
              <Icon icon={<ArrowDown />} label="Стрелка вниз" />
            </Key>
          </dt>
          <dd className="m-0">Выбрать объект в очереди</dd>
          <dt className="flex gap-1">
            <Key>
              <Icon icon={<ArrowLeft />} label="Стрелка влево" />
            </Key>
            <Key>
              <Icon icon={<ArrowRight />} label="Стрелка вправо" />
            </Key>
          </dt>
          <dd className="m-0">Выбрать объект в окружении</dd>
          <dt>
            <Key>Enter</Key>
          </dt>
          <dd className="m-0">Добавить кривую в контур</dd>
          <dt className="flex gap-1">
            <Key>PgUp</Key>
            <Key>PgDn</Key>
          </dt>
          <dd className="m-0">Перейти на страницу очереди</dd>
          <dt className="flex gap-1">
            <Key>Ctrl / Cmd</Key>
            <Key>Enter</Key>
          </dt>
          <dd className="m-0">Проверить выбранные кривые в AutoCAD</dd>
        </dl>
      </PopoverPopup>
    </Popover>
  );
}
