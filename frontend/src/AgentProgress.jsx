import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { agentName as publicAgentName } from './api';
import AgentExplanation from './AgentExplanation';
import AgentQuestion from './AgentQuestion';
import MonocleBrowser from './MonocleBrowser';
import { useMediaQuery } from '@/lib/utils';
import { CollapseSection, LiveHorizon, StageSwap } from '@/lib/motion';
import { Button } from '@/components/ui/button';
import { SelectField } from '@/components/ui/select';
import { Badge, Notice, Spinner, StatusIndicator } from '@/components/ui/surface';

const names = { monocle: 'Monocle', straus: 'Straus', context: 'Monocle', ameba: 'Ameba', arrodes: 'Arrodes' };
const baseProviderName = (provider) => names[String(provider ?? '').split(':')[0]] ?? provider;
const statuses = {
  running: 'Работает',
  completed: 'Завершено',
  failed: 'Ошибка',
  interrupted: 'Прервано',
  blocked: 'Нужны данные или доступ',
  unchanged: 'Без изменений',
};
const pageSize = 100;

export function AgentLog({ run, provider, maxHeightClass = 'max-h-[280px]' }) {
  const client = useQueryClient();
  const key = ['agent-log-view', run.id];
  const events = run.events ?? [];
  const saved = useRef(client.getQueryData(key) ?? { follow: true, top: 0, anchor: null });
  const [page, setPage] = useState(() => (saved.current.follow ? null : saved.current.start));
  const start = page == null ? Math.max(0, events.length - pageSize) : Math.min(page, Math.max(0, events.length - 1));
  const visible = events.slice(start, start + pageSize);
  const viewport = useRef(null);
  const content = useRef(null);
  const seen = useRef(new Map(events.map((event) => [event.id, event.message])));
  saved.current.start = start;

  function save() {
    const element = viewport.current;
    if (!element) return;
    const top = element.getBoundingClientRect().top;
    const row = [...element.querySelectorAll('[data-event-id]')].find((node) => node.getBoundingClientRect().bottom > top);
    saved.current = {
      ...saved.current,
      top: element.scrollTop,
      anchor: row ? { id: row.dataset.eventId, offset: row.getBoundingClientRect().top - top } : null,
    };
    client.setQueryData(key, saved.current);
  }

  function restore() {
    const element = viewport.current;
    if (!element) return;
    if (saved.current.follow) element.scrollTop = element.scrollHeight;
    else {
      const anchor = [...element.querySelectorAll('[data-event-id]')].find((node) => node.dataset.eventId === saved.current.anchor?.id);
      if (anchor) element.scrollTop += anchor.getBoundingClientRect().top - element.getBoundingClientRect().top - saved.current.anchor.offset;
      else element.scrollTop = saved.current.top;
    }
  }

  useLayoutEffect(() => {
    if (!content.current) return undefined;
    const observer = new ResizeObserver(restore);
    observer.observe(content.current);
    return () => observer.disconnect();
  }, []);

  useLayoutEffect(() => {
    restore();
    for (const event of events) seen.current.set(event.id, event.message);
  }, [events, start]);

  useLayoutEffect(() => () => save(), []);

  function go(next) {
    saved.current = { follow: next == null, top: 0, anchor: null, start: next ?? Math.max(0, events.length - pageSize) };
    client.setQueryData(key, saved.current);
    setPage(next);
  }

  return (
    <div className="flex flex-col gap-1">
      {events.length > pageSize && (
        <div className="flex flex-wrap items-center gap-1.5">
          <Button size="xs" variant="subtle" disabled={start === 0} onClick={() => go(Math.max(0, start - pageSize))}>Раньше</Button>
          <span className="text-[11px] text-[#929292] tabular-nums">
            {start + 1}–{Math.min(start + pageSize, events.length)} из {events.length}
          </span>
          <Button size="xs" variant="subtle" disabled={start + pageSize >= events.length} onClick={() => go(start + pageSize)}>Позже</Button>
          <Button size="xs" variant="subtle" onClick={() => go(null)}>К последним</Button>
        </div>
      )}
      <div
        ref={viewport}
        role="log"
        aria-live={saved.current.follow ? 'polite' : 'off'}
        className={`agent-log bg-[#0A0A0A] border border-[#242424] px-2.5 py-1.5 ${maxHeightClass}`}
        onScroll={(event) => {
          const element = event.currentTarget;
          saved.current.follow = start + pageSize >= events.length && element.scrollHeight - element.scrollTop - element.clientHeight < 32;
          if (!saved.current.follow && page == null) setPage(start);
          if (saved.current.follow && page != null) setPage(null);
          save();
        }}
      >
        <div ref={content}>
          {!events.length && (
            <div className="text-[12px] text-[#787878] py-1">
              {run.status === 'running' ? 'Ожидаем события агента…' : 'Журналов пока нет'}
            </div>
          )}
          {visible.map((event) => (
            <div key={event.id} data-event-id={String(event.id)} className="agent-log-row border-b border-[#161616] last:border-b-0">
              <div className="font-mono text-[11px] max-md:text-[12px] leading-relaxed whitespace-pre-wrap text-[#DCDCDC]">
                <span className="text-[#787878]">
                  {new Date(event.at).toLocaleTimeString()} · {publicAgentName(run.agent) ?? baseProviderName(provider)}{event.task_id ? ` #${event.task_id}` : ''} ·{' '}
                </span>
                {event.id === events.at(-1)?.id && saved.current.follow
                  ? <AgentExplanation text={event.message} initialText={seen.current.get(event.id) ?? ''} />
                  : event.message}
              </div>
              {event.description && (
                <div className="text-[11px] text-[#929292] mt-0.5 pl-2 border-l border-[#303030]">
                  {event.description}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function isBrowserActiveInRun(entry) {
  const run = entry?.run;
  if (!entry?.available || run?.status !== 'running') return false;
  return Boolean(
    run.browser_used
    || run.browser_waiting
    || run.events?.some((event) => /^(Входит через сохранённый вход|Читает (текущую )?страницу|Переходит по выбранной ссылке|Скачивает документ из браузера|Ищет в Google|Смотрит снимок браузера|Перемещает курсор|Нажимает|Вводит текст|Прокручивает страницу|Открывает страницу|Возвращается назад|Переходит вперёд|Рассматривает изображение|Ждёт, пока владелец завершит CAPTCHA)/.test(event.message)),
  );
}

export function useAgentProgressStream(taskId = 0, providers = []) {
  const client = useQueryClient();
  const providerList = [...providers].sort().join(',');
  const [streamError, setStreamError] = useState(false);

  const query = useQuery({
    queryKey: ['agent-progress', taskId, providerList],
    queryFn: async () => [],
    initialData: [],
    enabled: false,
    gcTime: Infinity,
  });

  useEffect(() => {
    for (const prefix of ['agent-run', 'agent-log-view', 'agent-progress-open', 'agent-progress-selected']) {
      client.setQueryDefaults([prefix], { gcTime: Infinity });
    }
  }, [client]);

  useEffect(() => {
    if (!providerList) return undefined;
    const key = ['agent-progress', taskId, providerList];
    const url = `/api/integrations/progress/stream?task_id=${taskId}&providers=${encodeURIComponent(providerList)}`;
    let stream = null;
    let lastAmebaStatus = null;
    let lastInvalidatedAmebaRun = null;

    function connect() {
      if (stream || document.hidden) return;
      stream = new EventSource(url);
      setStreamError(false);
      stream.onmessage = (event) => {
        try {
          const entries = JSON.parse(event.data);
          const amebaEntry = entries.find((item) => item.provider === 'ameba')?.run;
          if (amebaEntry) {
            const failedOrStopped = amebaEntry.status === 'failed' || amebaEntry.status === 'interrupted';
            client.setQueryData(['ameba-live-plan'], {
              running: amebaEntry.status === 'running',
              status: amebaEntry.status,
              started_at: amebaEntry.started_at,
              schedule: failedOrStopped ? null : (amebaEntry.schedule ?? null),
              timeline: failedOrStopped ? [] : (amebaEntry.timeline ?? []),
            });
            if (amebaEntry.status !== 'running' && lastAmebaStatus === 'running' && lastInvalidatedAmebaRun !== amebaEntry.id) {
              lastInvalidatedAmebaRun = amebaEntry.id;
              client.invalidateQueries({ queryKey: ['ameba-ranking'] });
              client.invalidateQueries({ queryKey: ['agent-queue'] });
            }
            lastAmebaStatus = amebaEntry.status;
          }
          client.setQueryData(key, (previous = []) => entries.map((entry) => {
            const previousRun = previous.find((item) => item.provider === entry.provider)?.run;
            const runId = entry.run?.id ?? previousRun?.id;
            const oldRun = (runId && client.getQueryData(['agent-run', runId])) ?? previousRun;
            let run = entry.run ?? (entry.available && oldRun?.status === 'running'
              ? { ...oldRun, status: 'interrupted', model_waiting: false, pending_question: null }
              : oldRun ?? null);
            if (run && oldRun?.id === run.id) {
              const merged = new Map((oldRun.events ?? []).map((item) => [item.id, item]));
              for (const item of run.events ?? []) merged.set(item.id, item);
              run = { ...run, events: [...merged.values()], browser_used: run.browser_used || oldRun.browser_used };
            }
            if (run) client.setQueryData(['agent-run', run.id], run);
            return { ...entry, run, receivedAt: performance.now() };
          }));
          setStreamError(false);
        } catch {
          setStreamError(true);
        }
      };
      stream.onerror = () => setStreamError(true);
    }

    function disconnect() {
      if (!stream) return;
      stream.close();
      stream = null;
    }

    function handleVisibility() {
      if (document.hidden) disconnect();
      else connect();
    }

    connect();
    document.addEventListener('visibilitychange', handleVisibility);
    window.addEventListener('pagehide', disconnect);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibility);
      window.removeEventListener('pagehide', disconnect);
      disconnect();
    };
  }, [client, taskId, providerList]);

  return { entries: query.data ?? [], streamError, providerList };
}

export default function AgentProgress({
  taskId = 0,
  providers = [],
  active = false,
  autoOpenOnActive = true,
  defaultOpen,
  hideSummaryWhenIdle = false,
  compactRunning = false,
  hideErrorNotice = false,
  open,
  onOpenChange,
}) {
  const client = useQueryClient();
  const compact = useMediaQuery('(max-width: 767px)', typeof window !== 'undefined' ? window.innerWidth < 768 : false);
  const { entries, streamError, providerList } = useAgentProgressStream(taskId, providers);
  const openKey = ['agent-progress-open', taskId, providerList];
  const selectionKey = ['agent-progress-selected', taskId, providerList];
  const [internalOpened, setInternalOpened] = useState(() => (
    new URLSearchParams(window.location.search).get('browser') === '1'
    || (client.getQueryData(openKey) ?? (defaultOpen !== undefined ? defaultOpen : !compact))
  ));
  const [selected, setSelected] = useState(() => client.getQueryData(selectionKey) ?? null);
  const isControlled = open !== undefined;
  const opened = isControlled ? Boolean(open) : internalOpened;

  const setOpenState = (next) => {
    client.setQueryData(openKey, next);
    if (!isControlled) setInternalOpened(next);
    onOpenChange?.(next);
  };

  useEffect(() => {
    if (active && autoOpenOnActive) {
      client.setQueryData(openKey, true);
      if (!isControlled) setInternalOpened(true);
      onOpenChange?.(true);
    }
  }, [active, autoOpenOnActive, client, taskId, providerList]);

  const runs = entries.filter((entry) => entry.run);
  const agentCounts = new Map();
  for (const entry of runs) {
    const label = publicAgentName(entry.run.agent) ?? baseProviderName(entry.provider);
    agentCounts.set(label, (agentCounts.get(label) ?? 0) + 1);
  }
  const entryLabel = (entry) => {
    const base = publicAgentName(entry.run.agent) ?? baseProviderName(entry.provider);
    return (agentCounts.get(base) ?? 0) > 1 && entry.run.task_id ? `${base} #${entry.run.task_id}` : base;
  };
  const current = runs.find((entry) => entry.provider === selected)
    ?? [...runs].sort((a, b) => Number(b.run.status === 'running') - Number(a.run.status === 'running') || (b.run.started_at ?? '').localeCompare(a.run.started_at ?? ''))[0];
  const run = current?.run;
  const queued = entries.some((entry) => entry.queued);
  const running = active || queued || runs.some((entry) => entry.run.status === 'running');
  const displayAgent = current ? entryLabel(current) : providers.map((provider) => baseProviderName(provider)).join(' · ');
  const browserWaiting = Boolean(run?.status === 'running' && run?.browser_waiting);
  const statusLabel = run?.pending_question
    ? 'Ждёт ответа'
    : browserWaiting
      ? 'Ждёт браузер'
      : run
        ? statuses[run.status] ?? run.status
        : queued
          ? 'В очереди'
          : active
            ? 'Запуск'
            : 'Готов';
  const statusBadgeVariant = run?.status === 'failed'
    ? 'danger'
    : run?.status === 'blocked' || run?.pending_question || browserWaiting
      ? 'warning'
      : (run ? run.status === 'running' : running)
        ? 'accent'
        : 'outline';
  const indicatorTone = run?.status === 'failed'
    ? 'danger'
    : run?.status === 'blocked' || run?.pending_question || browserWaiting
      ? 'warning'
      : running
        ? 'active'
        : run?.status === 'completed'
          ? 'success'
          : 'idle';

  const lastActivity = run?.last_activity_at ?? run?.events?.at(-1)?.at ?? run?.started_at;
  const serverTime = Date.parse(current?.server_time);
  const elapsed = current?.receivedAt == null ? 0 : Math.max(0, performance.now() - current.receivedAt);
  const quietMinutes = lastActivity && Number.isFinite(serverTime) ? Math.max(0, Math.floor((serverTime + elapsed - Date.parse(lastActivity)) / 60000)) : 0;
  const browserUsed = isBrowserActiveInRun(current);
  const hasTokens = Boolean(run?.token_usage && (run.token_usage.input_tokens != null || run.token_usage.output_tokens != null));
  const hasQuestion = entries.some((entry) => entry.run?.pending_question);
  const hasFailure = run?.status === 'failed';
  const horizonTone = hasFailure ? 'danger' : (hasQuestion || browserWaiting) ? 'warning' : 'accent';

  if (!providers.length) return null;
  if (
    hideSummaryWhenIdle
    && !running
    && !opened
    && !streamError
    && !hasQuestion
    && (!hasFailure || hideErrorNotice)
    && entries.every((e) => e.available)
  ) {
    return null;
  }

  if (compactRunning && !opened) {
    const compactText = run?.pending_question
      ? 'Ждёт ответа владельца'
      : run?.browser_waiting
        ? (run.current_action ?? 'Ждёт освобождения браузера')
        : run?.model_waiting
          ? 'Обдумывает план…'
          : (run?.agent_comment || run?.current_action || (queued ? 'В очереди на планирование…' : 'Составляет план…'));

    return (
      <section className="relative overflow-hidden border border-[#303030] bg-[#111111] px-3 py-1.5 flex flex-col gap-1.5">
        <LiveHorizon active={running && !run?.pending_question} tone={horizonTone} />
        <div className="flex items-center justify-between gap-2 min-h-[26px] min-w-0">
          <div className="flex items-center gap-2 min-w-0 flex-1">
            <StatusIndicator tone={indicatorTone} pulse={running && !run?.pending_question} />
            <span className="text-[12px] text-[#EAEAEA] truncate" role="status" aria-live="polite">
              <AgentExplanation text={compactText} />
            </span>
          </div>
          <Button
            size="xs"
            variant="subtle"
            aria-expanded={opened}
            onClick={() => setOpenState(true)}
            className="gap-1.5 shrink-0"
          >
            {running && !run?.pending_question && <Spinner size={11} />}
            <span>Журнал</span>
            {run?.events?.length ? <span className="text-[11px] text-[#929292] tabular-nums">({run.events.length})</span> : null}
            <ChevronDown className="h-3.5 w-3.5" />
          </Button>
        </div>

        {hasFailure && !hideErrorNotice && (
          <Notice tone="error">{run.error || 'Агент завершился с ошибкой. Повторите поручение.'}</Notice>
        )}

        {entries.filter((entry) => entry.run?.pending_question).map((entry) => (
          <AgentQuestion key={entry.run.pending_question.id} question={entry.run.pending_question} agent={entry.run.agent} />
        ))}

        {entries.filter((entry) => !entry.available).map((entry) => (
          <div key={entry.provider} className="text-[11px] text-[#D4A24C]">
            {names[entry.provider] ?? entry.provider}: журнал пока недоступен
          </div>
        ))}

        {streamError && <Notice tone="warning">Соединение с журналом потеряно. Переподключаемся…</Notice>}
      </section>
    );
  }

  return (
    <section className="relative overflow-hidden border border-[#303030] bg-[#111111] px-3 py-2 flex flex-col gap-2">
      <LiveHorizon active={running && !run?.pending_question} tone={horizonTone} />
      <div className="flex flex-wrap items-center justify-between gap-2 min-h-[28px]">
        <div className="flex flex-wrap items-center gap-2 min-w-0">
          <StatusIndicator tone={indicatorTone} pulse={running && !run?.pending_question} />
          {runs.length > 1 ? (
            <SelectField
              size="sm"
              className="w-[132px] sm:w-[150px]"
              ariaLabel="Журнал агента"
              value={current.provider}
              options={runs.map((entry) => ({
                value: entry.provider,
                label: entryLabel(entry),
              }))}
              onChange={(value) => {
                setSelected(value);
                client.setQueryData(selectionKey, value);
              }}
            />
          ) : (
            <span className="text-[13px] font-medium text-[#EAEAEA] truncate">{displayAgent}</span>
          )}
          <Badge variant={statusBadgeVariant}>{statusLabel}</Badge>
          {hasTokens && (
            <span className="text-[11px] text-[#929292] tabular-nums hidden sm:inline">
              {run.token_usage.input_tokens ?? 0} / {run.token_usage.output_tokens ?? 0} tok
            </span>
          )}
        </div>

        <Button
          size="sm"
          variant="subtle"
          aria-expanded={opened}
          onClick={() => setOpenState(!opened)}
          className="gap-1.5"
        >
          {running && !run?.pending_question && <Spinner size={12} />}
          <span>Журнал</span>
          {run?.events?.length ? <span className="text-[11px] text-[#929292] tabular-nums">({run.events.length})</span> : null}
          {opened ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
        </Button>
      </div>

      {run?.status === 'running' && (
        <div className="flex flex-col gap-1 border-t border-[#222222] pt-2" key={run.id}>
          <StageSwap stageKey={run.pending_question ? 'q' : run.browser_waiting ? 'browser-wait' : run.model_waiting ? 'wait' : (run.current_action ?? 'step')}>
            <div className="flex items-start gap-2 text-[13px]" role="status" aria-live="polite">
              {run.pending_question ? (
                <span className="text-[#D4A24C]">Ждёт ответа</span>
              ) : run.browser_waiting ? (
                <span className="text-[#D4A24C]">
                  <AgentExplanation text={run.current_action ?? 'Ждёт освобождения браузера'} />
                </span>
              ) : run.model_waiting ? (
                <span className="agent-thinking">Думает</span>
              ) : (
                <span className="text-[#EAEAEA]">
                  <AgentExplanation text={run.current_action ?? 'Выполняет шаг'} />
                </span>
              )}
            </div>
          </StageSwap>
          {run.action_description && !run.model_waiting && !run.pending_question && (
            <div className="text-[12px] text-[#929292]">{run.action_description}</div>
          )}
          {run.agent_comment && (
            <div className="text-[12px] text-[#D8C2EE] border-l-2 border-[#7F49B4] pl-2 py-0.5 whitespace-pre-wrap break-words">
              <AgentExplanation text={run.agent_comment} />
            </div>
          )}
        </div>
      )}

      {run?.status === 'failed' && (
        <Notice tone="error">{run.error || 'Агент завершился с ошибкой. Повторите поручение.'}</Notice>
      )}

      {entries.filter((entry) => entry.run?.pending_question).map((entry) => (
        <AgentQuestion key={entry.run.pending_question.id} question={entry.run.pending_question} agent={entry.run.agent} />
      ))}

      <CollapseSection open={opened}>
        <div className={`pt-1 grid gap-3 items-start ${browserUsed ? 'grid-cols-1 md:grid-cols-[minmax(240px,34%)_minmax(0,1fr)]' : 'grid-cols-1'}`}>
          {browserUsed && (
            <aside className="min-w-0">
              <MonocleBrowser key={run.id} active />
            </aside>
          )}
          <div className="min-w-0 flex flex-col gap-1.5">
            {hasTokens && (
              <div className="text-[11px] text-[#929292] tabular-nums">
                {run.token_usage.input_tokens ?? 0} / {run.token_usage.output_tokens ?? 0} tok
              </div>
            )}
            {current?.available && !streamError && run?.status === 'running' && !run.pending_question && quietMinutes >= 2 && (
              <Notice tone="warning">Агент не передавал новых данных {quietMinutes} мин.</Notice>
            )}
            {run ? (
              <AgentLog key={run.id} run={run} provider={current.provider} />
            ) : (
              <div className="text-[12px] text-[#929292] border border-[#242424] bg-[#0A0A0A] px-2.5 py-2">
                {running ? 'Ожидаем события агента…' : 'Журналов пока нет'}
              </div>
            )}
          </div>
        </div>
      </CollapseSection>

      {entries.filter((entry) => !entry.available).map((entry) => (
        <div key={entry.provider} className="text-[11px] text-[#D4A24C]">
          {names[entry.provider] ?? entry.provider}: журнал пока недоступен
        </div>
      ))}

      {streamError && <Notice tone="warning">Соединение с журналом потеряно. Переподключаемся…</Notice>}
    </section>
  );
}
