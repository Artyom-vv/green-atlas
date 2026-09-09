import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowUp, ChevronRight, MessagesSquare, MessageSquare, PanelLeftClose, Plus, Search, Square, Undo2 } from 'lucide-react';
import { Button, IconButton, Select } from '@green/ui';
import { useLocation, useNavigate } from 'react-router-dom';
import { ResizeHandle } from '../workspace/ResizeHandle';
import { useProjectAssistant } from './assistantContext';
import { AgentActivity } from './AgentActivity';
import './project-assistant.css';
import { repeatedItemLabel } from '../../domain-ui/plantingZoneLabels';
import { AutonomousAgentPanel } from './AutonomousAgentPanel';

export function ProjectAssistantTrigger({ disabled = false, onOpen }: { disabled?: boolean; onOpen?: () => void }) {
  const chat = useProjectAssistant();
  return <Button className="project-assistant-trigger" icon={MessageSquare} variant={chat.open ? 'primary' : 'secondary'} disabled={disabled} aria-expanded={chat.open} aria-controls="project-assistant" onClick={() => { if (!chat.open) onOpen?.(); chat.setOpen(!chat.open); }}>Помощник</Button>;
}

export function ProjectAssistantSidebar() {
  const chat = useProjectAssistant();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const history = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const follow = useRef(true);
  const [zoneId, setZoneId] = useState('');
  const [search, setSearch] = useState('');
  const [mode, setMode] = useState<'legacy' | 'autonomous'>('legacy');
  const onMap = pathname.endsWith('/workspace');
  const busy = Boolean(chat.pending);
  const stoppable = chat.pending === 'thinking' || chat.pending === 'preparing';
  const proposal = chat.proposal;
  const proposalZone = chat.project?.planting_zones?.find(zone => zone.id === proposal?.intent.zone_id);
  const proposalScope = proposalZone ? repeatedItemLabel(chat.project?.planting_zones ?? [], proposalZone).replace(/\s*[·•]\s*/g, ' ') : '';
  const receipt = chat.undoReceipt;
  const canUndo = receipt && receipt.version === chat.project?.plan?.version && receipt.stateVersion === chat.project?.state_version;
  useEffect(() => {
    if (!chat.open) return;
    composer.current?.focus();
    if (follow.current && history.current) history.current.scrollTop = history.current.scrollHeight;
  }, [chat.open, chat.listOpen, chat.activeConversationId]);
  useEffect(() => {
    if (follow.current && history.current) history.current.scrollTop = history.current.scrollHeight;
  }, [chat.messages, chat.pending, chat.proposal, chat.pendingIntent, chat.error]);
  useEffect(() => setZoneId(''), [chat.pendingIntent?.messageId]);
  const close = () => { chat.setOpen(false); requestAnimationFrame(() => document.querySelector<HTMLButtonElement>('.project-assistant-trigger')?.focus()); };
  return <aside id="project-assistant" className="project-assistant" aria-label={mode === 'autonomous' ? 'Автономный агент' : 'Чат проекта'} hidden={!chat.open}>
    {mode === 'autonomous' ? <AutonomousAgentPanel projectId={chat.projectId} onBack={() => setMode('legacy')} onClose={close} /> : <>
    <header className="project-assistant__header">
      <IconButton icon={chat.listOpen ? ArrowLeft : MessagesSquare} variant="ghost" label={chat.listOpen ? 'Вернуться в диалог' : 'Диалоги проекта'} disabled={busy} onClick={() => chat.listOpen ? chat.closeConversations() : void chat.showConversations()} />
      <h2 title={chat.listOpen ? 'Диалоги проекта' : chat.conversationTitle}>{chat.listOpen ? 'Диалоги проекта' : chat.conversationTitle}</h2><Button className="project-assistant__agent-mode" controlSize="compact" variant="ghost" onClick={() => setMode('autonomous')}>Агент</Button><IconButton icon={PanelLeftClose} variant="ghost" label="Закрыть чат" onClick={close} />
    </header>
    {chat.listOpen ? <>
      <div className="project-assistant__list-tools">
        <label className="project-assistant__search"><Search size={18} aria-hidden="true" /><input aria-label="Найти диалог" placeholder="Найти диалог" value={search} onChange={event => setSearch(event.target.value)} /></label>
        <Button icon={Plus} variant="primary" disabled={busy} onClick={() => void chat.newConversation()}>Новый диалог</Button>
      </div>
      <nav className="project-assistant__conversations" aria-label="Диалоги проекта">
        {chat.pending ? <p role="status">Загрузка диалогов…</p> : null}
        {chat.error ? <div className="project-assistant__error" role="alert"><p>{chat.error}</p><Button variant="secondary" disabled={busy} onClick={() => void chat.showConversations()}>Обновить список</Button></div> : null}
        {chat.conversations.filter(item => item.title.toLocaleLowerCase('ru').includes(search.trim().toLocaleLowerCase('ru'))).map(item => <button type="button" className="project-assistant__conversation" key={item.id} aria-current={item.id === chat.activeConversationId ? 'page' : undefined} disabled={busy} onClick={() => void chat.selectConversation(item.id)}>
          <span><strong title={item.title}>{item.title}</strong><small>{Number.isNaN(Date.parse(item.updated_at)) ? 'Диалог проекта' : new Date(item.updated_at).toLocaleString('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' })}</small></span><ChevronRight size={18} aria-hidden="true" />
        </button>)}
        {!busy && !chat.error && !chat.conversations.some(item => item.title.toLocaleLowerCase('ru').includes(search.trim().toLocaleLowerCase('ru'))) ? <p>{search.trim() ? 'Диалоги не найдены' : 'Пока нет диалогов'}</p> : null}
      </nav>
    </> : <>
    <div className="project-assistant__history" ref={history} role="log" aria-label="Переписка по проекту" aria-live="polite" onScroll={event => { const el = event.currentTarget; follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 64; }}>
      {!chat.messages.length ? <div className="project-assistant__welcome"><h3>Что изменим в проекте?</h3><p>Опишите задачу. Сначала покажу предложение на карте.</p><div className="project-assistant__suggestions"><Button variant="secondary" onClick={() => { chat.setDraft('Посади группы деревьев вдоль зданий'); composer.current?.focus(); }}>Посадить вдоль зданий</Button><Button variant="secondary" onClick={() => { chat.setDraft('Покажи рост через 20 лет'); composer.current?.focus(); }}>Посмотреть рост</Button></div></div> : null}
      {chat.messages.map(line => <article key={line.id} className={`project-assistant__message is-${line.role}`} aria-label={line.role === 'user' ? 'Вы' : 'Помощник'}>
        {line.text ? <p>{line.text}</p> : null}
        {line.activity ? <AgentActivity trace={line.activity} /> : null}
        {line.result && proposal?.messageId !== line.id ? <p className="project-assistant__pending">{line.result}</p> : null}
        {proposal?.messageId === line.id ? <section className="project-assistant__proposal" aria-label="Предложение">
          <h3>{proposal.stale ? 'План изменился' : `${proposal.title}${proposal.intent.action === 'delete' ? '?' : ''}`}</h3>
          <p>{proposal.stale ? 'Пересчитайте предложение для текущего плана.' : proposal.scopeLabel ?? (proposal.intent.scope === 'project' ? 'Весь проект' : proposal.intent.scope === 'selection' ? 'Выбранные посадки' : proposalScope)}</p>
          {!proposal.stale && proposal.verificationNotice ? <p>{proposal.verificationNotice}</p> : null}
          {!proposal.stale && proposal.shortfallExplanation ? <p>{proposal.shortfallExplanation}</p> : null}
          {!proposal.stale && !proposal.preview.can_apply ? <p>Это размещение нельзя применить. Измените запрос.</p> : null}
          <AgentActivity trace={proposal.agentTrace} />
          <div className="project-assistant__actions">
            {proposal.stale ? <Button variant="primary" disabled={busy} onClick={chat.recalculate}>Пересчитать</Button> : !onMap ? <Button variant="primary" onClick={() => navigate(`/projects/${chat.projectId}/workspace`)}>Показать на карте</Button> : <Button variant={proposal.intent.action === 'delete' ? 'danger' : 'primary'} disabled={busy || !proposal.preview.can_apply} onClick={() => void chat.apply()}>{chat.pending === 'applying' ? 'Применяем…' : proposal.intent.action === 'delete' ? 'Удалить' : 'Применить'}</Button>}
            <Button variant="secondary" disabled={busy} onClick={chat.discard}>{proposal.intent.action === 'delete' ? 'Оставить' : 'Отказаться'}</Button>
          </div>
        </section> : null}
        {chat.pendingIntent?.messageId === line.id ? <section className="project-assistant__proposal"><label className="project-assistant__field">На каком участке?<Select aria-label="Участок для предложения" value={zoneId} onChange={event => setZoneId(event.target.value)}><option value="">Выберите участок</option>{chat.project?.planting_zones?.map(zone => <option key={zone.id} value={zone.id}>{repeatedItemLabel(chat.project?.planting_zones ?? [], zone).replace(/\s*[·•]\s*/g, ' ')}</option>)}<option value="__project__">Весь проект</option></Select></label><Button variant="primary" disabled={!zoneId || busy} onClick={() => chat.chooseZone(zoneId)}>Показать предложение</Button></section> : null}
        {receipt?.messageId === line.id && canUndo ? <Button variant="secondary" icon={Undo2} disabled={busy} onClick={() => void chat.undo()}>Отменить изменение</Button> : null}
      </article>)}
      {chat.pending ? <AgentActivity pending={chat.pending} /> : null}
      {chat.error ? <div className="project-assistant__error" role="alert"><p>{chat.error}</p><Button variant="secondary" disabled={busy || Boolean(proposal?.stale)} onClick={chat.retry}>Повторить</Button></div> : null}
    </div>
    <form className="project-assistant__composer" onSubmit={event => { event.preventDefault(); follow.current = true; void chat.send(); }}>
      <span className="project-assistant__scope">{chat.selectedIds.length && chat.selectedZoneIds.length ? 'Выбраны участки и посадки' : chat.selectedIds.length ? `Выбрано посадок: ${chat.selectedIds.length}` : chat.selectedZoneIds.length ? `Выбрано участков: ${chat.selectedZoneIds.length}` : 'Проект без выделения'}</span>
      <div className="project-assistant__input"><textarea ref={composer} aria-label="Сообщение помощнику" placeholder="Что нужно сделать?" value={chat.draft} maxLength={2000} rows={3} onChange={event => chat.setDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (!busy) { follow.current = true; void chat.send(); } } }} />
        {stoppable ? <IconButton icon={Square} variant="secondary" label="Остановить запрос" onClick={chat.stop} /> : <IconButton icon={ArrowUp} variant="primary" label="Отправить сообщение" type="submit" disabled={busy || !chat.draft.trim() || !chat.project} />}
      </div>
    </form>
    </>}
    </>}
    <ResizeHandle className="project-assistant__resize" label="Ширина чата" orientation="vertical" value={chat.width} min={chat.minWidth} max={chat.maxWidth} onChange={chat.setWidth} onReset={chat.resetWidth} />
  </aside>;
}
