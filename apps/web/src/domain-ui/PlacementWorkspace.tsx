import { useState, type ReactNode } from 'react';
import { Button, IconButton } from '@green/ui';
import { ArrowLeft, PanelRightClose, SlidersHorizontal, Sparkles } from 'lucide-react';
import './placement-workspace.css';

/** A modeless task surface. The map remains the result area, never a backdrop. */
export function PlacementWorkspace({ open, hasPreview, busy = false, recommendation, onRecommendation, onClose, manual, automatic }: {
  open: boolean; hasPreview: boolean; recommendation: boolean;
  busy?: boolean;
  onRecommendation: (value: boolean) => void; onClose: () => void;
  manual: ReactNode; automatic: ReactNode;
}) {
  const [chosen, setChosen] = useState(false);
  const choose = (value: boolean) => { onRecommendation(value); setChosen(true); };
  const choosing = !chosen && !hasPreview;
  return <section className="placement-workspace" hidden={!open} aria-label="Размещение посадок">
    <header className="placement-workspace__header">{!choosing ? <IconButton icon={ArrowLeft} label="Способ подбора" variant="ghost" disabled={busy || hasPreview} onClick={() => setChosen(false)} /> : null}<h2>{choosing ? 'Размещение посадок' : recommendation ? 'Подбор по задаче' : 'Размещение посадок'}</h2><IconButton icon={PanelRightClose} label="Свернуть размещение" variant="ghost" onClick={onClose} /></header>
    {choosing ? <div className="placement-workspace__choice"><h3>Как подобрать посадки?</h3><div className="placement-workspace__options">
      <Button variant="secondary" icon={SlidersHorizontal} disabled={busy} onClick={() => choose(false)}>Выбрать состав и количество</Button>
      <Button variant="secondary" icon={Sparkles} disabled={busy} onClick={() => choose(true)}>Подобрать по задаче</Button>
    </div><p>Сначала настройки, затем проверка на карте. План изменится только после добавления.</p></div> : null}
    <div className="placement-workspace__flow" hidden={choosing}>
      <div className="placement-workspace__module" hidden={recommendation}>{manual}</div>
      <div className="placement-workspace__module" hidden={!recommendation}>{automatic}</div>
    </div>
  </section>;
}
