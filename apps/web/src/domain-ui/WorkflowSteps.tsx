import { Check } from 'lucide-react';

/** One compact route; state is explicit without adding navigation tabs. */
export function WorkflowSteps({ labels, current, label }: { labels: string[]; current: number; label: string }) {
  return <ol className="placement-steps" style={{ gridTemplateColumns: `repeat(${labels.length}, minmax(max-content, 1fr))` }} aria-label={label}>
    {labels.map((item, index) => <li key={item} data-state={index < current ? 'complete' : index === current ? 'current' : 'upcoming'} aria-current={index === current ? 'step' : undefined} aria-label={`${index + 1}. ${item}${index < current ? ', завершён' : index === current ? ', текущий шаг' : ''}`}>
      <span className="placement-steps__track" aria-hidden="true" />
      <span className="placement-steps__name"><Check size={12} strokeWidth={2} aria-hidden="true" />{item}</span>
    </li>)}
  </ol>;
}
