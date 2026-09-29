import type { FC, ReactNode } from 'react';
import { Text } from '@green/ui';

const PROJECT_STEPS = [
  'Исходные данные',
  'Слои и ограничения',
  'Участки и посадки',
] as const;

export interface ProjectStepsProps {
  active: 1 | 2 | 3;
  projectName?: string;
}

export const ProjectSteps: FC<ProjectStepsProps> = ({
  active,
  projectName = 'Без названия',
}) => (
  <aside className="flex min-h-0 min-w-0 flex-col overflow-y-auto border-r border-neutral-200 bg-white">
    <Text variant="label" className="flex min-h-14 items-center px-4 text-sm">
      Новый проект
    </Text>
    <nav aria-label="Этапы проекта">
      <ol className="m-0 list-none p-0">
        {PROJECT_STEPS.map((label, index) => (
          <li
            aria-current={active === index + 1 ? 'step' : undefined}
            className="flex min-h-12 items-center gap-3 border-l-2 border-transparent px-4 aria-[current=step]:border-blue-600 aria-[current=step]:bg-blue-100"
            key={label}
          >
            <Text
              mono
              variant="caption"
              className={
                active === index + 1
                  ? 'text-blue-700'
                  : active > index + 1
                    ? 'text-green-700'
                    : 'text-neutral-400'
              }
            >
              {String(index + 1).padStart(2, '0')}
            </Text>
            <Text
              variant="label"
              className={
                active === index + 1
                  ? 'text-neutral-800'
                  : 'font-normal text-neutral-500'
              }
            >
              {label}
            </Text>
          </li>
        ))}
      </ol>
    </nav>
    <div className="flex-1" />
    <div className="flex min-h-20 flex-col justify-center gap-1 border-t border-neutral-200 px-4">
      <Text variant="caption">Проект</Text>
      <Text variant="label" className="truncate">
        {projectName || 'Без названия'}
      </Text>
    </div>
  </aside>
);

export interface FlowDocumentProps {
  layout?: 'card' | 'panels';
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  header?: ReactNode;
}

export const FlowDocument: FC<FlowDocumentProps> = ({
  layout = 'card',
  title,
  description,
  children,
  footer,
  header,
}) => (
  <section
    className={`min-h-0 min-w-0 flex-1 ${layout === 'panels' ? 'bg-white' : 'bg-neutral-100 p-4'}`}
  >
    <div
      className={`flex h-full w-full flex-col overflow-hidden bg-white ${layout === 'card' ? 'rounded-dialog border border-neutral-200' : ''}`}
    >
      {header === undefined ? (
        <header
          className={`flex shrink-0 flex-col justify-center gap-2 border-b border-neutral-200 ${layout === 'panels' ? 'px-4 py-5 sm:px-6' : 'min-h-29 px-8 py-6'}`}
        >
          <Text as="h1" variant="pageHeading">
            {title}
          </Text>
          {description ? (
            <Text as="p" tone="muted" className="max-w-165">
              {description}
            </Text>
          ) : null}
        </header>
      ) : (
        header
      )}
      <div
        className={`min-h-0 flex-1 overflow-auto overscroll-contain [scrollbar-gutter:stable] ${layout === 'card' ? 'px-8 py-6' : ''}`}
      >
        {children}
      </div>
      {footer ? (
        <footer
          className={`flex min-h-16 shrink-0 flex-wrap items-center justify-end gap-2 border-t border-neutral-200 py-3 ${layout === 'panels' ? 'px-4 sm:px-6' : 'px-8'}`}
        >
          {footer}
        </footer>
      ) : null}
    </div>
  </section>
);
