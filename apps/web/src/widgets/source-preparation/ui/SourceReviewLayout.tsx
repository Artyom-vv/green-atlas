import type { ReactNode, Ref } from 'react';
import { Button, Text } from '@green/ui';

export interface SourceReviewSection {
  id: string;
  label: string;
  title: string;
  description?: ReactNode;
}

export interface SourceReviewLayoutProps {
  sections: readonly SourceReviewSection[];
  activeSection: string;
  onSectionChange: (section: string) => void;
  navRef?: Ref<HTMLElement>;
  headerActions?: ReactNode;
  headerProgress?: ReactNode;
  status?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
}

/** Presentation shared by source review and preparation; each owns its state. */
export function SourceReviewLayout({
  sections,
  activeSection,
  onSectionChange,
  navRef,
  headerActions,
  headerProgress,
  status,
  footer,
  children,
}: SourceReviewLayoutProps) {
  const active =
    sections.find((section) => section.id === activeSection) ?? sections[0];
  return (
    <div className="grid min-h-0 min-w-0 flex-1 grid-cols-[168px_minmax(0,1fr)] text-sm leading-5 max-[939px]:grid-cols-[minmax(0,1fr)] max-[939px]:grid-rows-[auto_minmax(0,1fr)]">
      <nav
        ref={navRef}
        className="flex flex-col gap-1 border-r border-neutral-200 bg-neutral-100 px-3 py-6 max-[939px]:flex-row max-[939px]:border-r-0 max-[939px]:border-b max-[939px]:py-2"
        aria-label="Исходные данные"
      >
        {sections.map((section, index) => (
          <Button
            key={section.id}
            variant="ghost"
            className="w-full justify-start px-3 aria-[current=page]:bg-blue-100 aria-[current=page]:text-blue-600 max-[939px]:w-auto"
            data-section={section.id}
            aria-current={activeSection === section.id ? 'page' : undefined}
            onClick={() => onSectionChange(section.id)}
          >
            <span
              aria-hidden="true"
              className="mr-2 text-xs text-neutral-500 tabular-nums max-[719px]:hidden"
            >
              {String(index + 1).padStart(2, '0')}
            </span>
            {section.label}
          </Button>
        ))}
      </nav>
      <section
        className="flex min-h-0 min-w-0 flex-col bg-white"
        aria-label="Исходные данные проекта"
      >
        <header className="relative flex shrink-0 flex-wrap items-start justify-between gap-4 p-6 max-[719px]:p-4">
          <div className="min-w-0 flex-1">
            <Text as="h1" variant="pageHeading">
              {active?.title}
            </Text>
            {active?.description && (
              <Text as="p" tone="muted">
                {active.description}
              </Text>
            )}
          </div>
          {headerActions}
          {headerProgress}
        </header>
        <div className="min-h-0 flex-1 overflow-auto overscroll-contain pb-6 [scrollbar-gutter:stable]">
          <div className="px-6 pb-4 empty:hidden max-[719px]:px-4">
            {status}
          </div>
          {children}
        </div>
        {footer != null && (
          <footer className="shrink-0 border-t border-neutral-200 bg-neutral-100 px-6 py-3 max-[719px]:min-h-[105px] max-[719px]:px-4">
            {footer}
          </footer>
        )}
      </section>
    </div>
  );
}
