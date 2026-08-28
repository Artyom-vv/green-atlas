import type { ReactNode } from 'react';

export function ProjectSteps({ active, projectName = 'Без названия' }: { active: 1 | 2 | 3; projectName?: string }) {
  const steps = [
    ['01', 'Импорт DXF'],
    ['02', 'Слои и ограничения'],
    ['03', 'Участки и посадки'],
  ] as const;
  return (
    <aside className="project-steps">
      <div className="project-steps__title">Новый проект</div>
      <nav aria-label="Этапы проекта">
        {steps.map(([number, label], index) => <div className={`project-step ${active === index + 1 ? 'is-active' : ''} ${active > index + 1 ? 'is-complete' : ''}`} key={number}><span>{number}</span><strong>{label}</strong></div>)}
      </nav>
      <div className="project-steps__spacer" />
      <div className="project-steps__meta"><span>Проект</span><strong>{projectName || 'Без названия'}</strong></div>
    </aside>
  );
}

export function FlowDocument({ title, description, children, footer }: { title: string; description: string; children: ReactNode; footer?: ReactNode }) {
  return (
    <section className="flow-stage">
      <div className="flow-document">
        <header className="flow-document__header"><h1>{title}</h1><p>{description}</p></header>
        <div className="flow-document__content">{children}</div>
        {footer ? <footer className="flow-document__footer">{footer}</footer> : null}
      </div>
    </section>
  );
}
