import { agentName as publicAgentName, request } from './api';
import AgentExplanation from './AgentExplanation';
import AgentQuestion from './AgentQuestion';
import MonocleBrowser from './MonocleBrowser';
import AgentTimeline from './AgentTimeline';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useMediaQuery } from '@mantine/hooks';
import { Alert, Badge, Button, Group, Loader, Modal, Paper, Select, Stack, Text } from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';

const names = { monocle: 'Monocle', straus: 'Straus', context: 'Monocle', ameba: 'Ameba', arrodes: 'Arrodes' };
const statuses = { running: 'Работает', completed: 'Завершено', failed: 'Ошибка', interrupted: 'Прервано', blocked: 'Нужны данные или доступ', unchanged: 'Без изменений' };
const pageSize = 100;

function AgentLog({ run, provider }) {
  const client = useQueryClient();
  const key = ['agent-log-view', run.id];
  const events = run.events ?? [];
  const saved = useRef(client.getQueryData(key) ?? { follow: true, top: 0, anchor: null });
  const [page, setPage] = useState(() => saved.current.follow ? null : saved.current.start);
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
    saved.current = { ...saved.current, top: element.scrollTop,
      anchor: row ? { id: row.dataset.eventId, offset: row.getBoundingClientRect().top - top } : null };
    client.setQueryData(key, saved.current);
  }
  useLayoutEffect(() => {
    const element = viewport.current;
    if (!element) return;
    function restore() {
      if (saved.current.follow) element.scrollTop = element.scrollHeight;
      else {
        const anchor = [...element.querySelectorAll('[data-event-id]')].find((node) => node.dataset.eventId === saved.current.anchor?.id);
        if (anchor) element.scrollTop += anchor.getBoundingClientRect().top - element.getBoundingClientRect().top - saved.current.anchor.offset;
        else element.scrollTop = saved.current.top;
      }
    }
    restore();
    for (const event of events) seen.current.set(event.id, event.message);
    const observer = new ResizeObserver(restore);
    observer.observe(content.current);
    return () => observer.disconnect();
  }, [events, start]);
  useLayoutEffect(() => () => save(), []);
  function go(next) {
    saved.current = { follow: next == null, top: 0, anchor: null, start: next ?? Math.max(0, events.length - pageSize) };
    client.setQueryData(key, saved.current);
    setPage(next);
  }
  return <Stack gap={4}>
    {events.length > pageSize && <Group gap="xs">
      <Button size="compact-xs" variant="subtle" disabled={start === 0} onClick={() => go(Math.max(0, start - pageSize))}>Раньше</Button>
      <Text size="xs" c="dimmed">{start + 1}–{Math.min(start + pageSize, events.length)} из {events.length}</Text>
      <Button size="compact-xs" variant="subtle" disabled={start + pageSize >= events.length} onClick={() => go(start + pageSize)}>Позже</Button>
      <Button size="compact-xs" variant="subtle" onClick={() => go(null)}>К последним</Button>
    </Group>}
    <div ref={viewport} role="log" aria-live={saved.current.follow ? 'polite' : 'off'} className="agent-log" onScroll={(event) => {
      const element = event.currentTarget;
      saved.current.follow = start + pageSize >= events.length && element.scrollHeight - element.scrollTop - element.clientHeight < 32;
      if (!saved.current.follow && page == null) setPage(start);
      if (saved.current.follow && page != null) setPage(null);
      save();
    }}>
      <div ref={content}>
        {!events.length && <Text size="xs" c="dimmed">{run.status === 'running' ? 'Ожидаем события агента…' : 'Журналов пока нет'}</Text>}
        {visible.map((event) => <div key={event.id} data-event-id={String(event.id)} className="agent-log-row">
          <Text size="xs" style={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
            <Text component="span" inherit c="dimmed">{new Date(event.at).toLocaleTimeString()} · {publicAgentName(run.agent) ?? names[provider]}{event.task_id ? ` #${event.task_id}` : ''} · </Text>
            {event.id === events.at(-1)?.id && saved.current.follow
              ? <AgentExplanation text={event.message} initialText={seen.current.get(event.id) ?? ''} /> : event.message}
          </Text>
        </div>)}
      </div>
    </div>
  </Stack>;
}

export default function AgentProgress({ taskId = 0, providers = [], active = false }) {
  const client = useQueryClient();
  const compact = useMediaQuery('(max-width: 767px)', window.innerWidth < 768);
  const providerList = [...providers].sort().join(',');
  const openKey = ['agent-progress-open', taskId, providerList];
  const selectionKey = ['agent-progress-selected', taskId, providerList];
  const [opened, setOpened] = useState(() => new URLSearchParams(window.location.search).get('browser') === '1' || (client.getQueryData(openKey) ?? !compact));
  const [selected, setSelected] = useState(() => client.getQueryData(selectionKey) ?? null);
  const [expanded, setExpanded] = useState(false);
  const [streamError, setStreamError] = useState(false);
  const query = useQuery({ queryKey: ['agent-progress', taskId, providerList], queryFn: async () => [], initialData: [], enabled: false, gcTime: Infinity });
  const ranking = useQuery({ queryKey: ['ameba-ranking'], queryFn: () => request('/integrations/ameba/ranking'), enabled: providers.includes('ameba'), refetchInterval: providers.includes('ameba') ? 5000 : false });
  useEffect(() => {
    for (const prefix of ['agent-run', 'agent-log-view', 'agent-progress-open', 'agent-progress-selected']) client.setQueryDefaults([prefix], { gcTime: Infinity });
  }, [client]);
  useEffect(() => {
    if (!providerList) return;
    const key = ['agent-progress', taskId, providerList];
    const stream = new EventSource(`/api/integrations/progress/stream?task_id=${taskId}&providers=${encodeURIComponent(providerList)}`);
    setStreamError(false);
    stream.onmessage = (event) => {
      try {
        const entries = JSON.parse(event.data);
        client.setQueryData(key, (previous = []) => entries.map((entry) => {
          const previousRun = previous.find((item) => item.provider === entry.provider)?.run;
          const runId = entry.run?.id ?? previousRun?.id;
          const oldRun = (runId && client.getQueryData(['agent-run', runId])) ?? previousRun;
          let run = entry.run ?? (entry.available && oldRun?.status === 'running'
            ? { ...oldRun, status: 'interrupted', model_waiting: false, pending_question: null } : oldRun ?? null);
          if (run && oldRun?.id === run.id) {
            const merged = new Map((oldRun.events ?? []).map((item) => [item.id, item]));
            for (const item of run.events ?? []) merged.set(item.id, item);
            run = { ...run, events: [...merged.values()], browser_used: run.browser_used || oldRun.browser_used };
          }
          if (run) client.setQueryData(['agent-run', run.id], run);
          return { ...entry, run, receivedAt: performance.now() };
        }));
        setStreamError(false);
      } catch { setStreamError(true); }
    };
    stream.onerror = () => setStreamError(true);
    return () => stream.close();
  }, [client, taskId, providerList]);
  useEffect(() => {
    if (active) { client.setQueryData(openKey, true); setOpened(true); }
  }, [active, client, taskId, providerList]);
  const entries = query.data ?? [];
  const runs = entries.filter((entry) => entry.run);
  const current = runs.find((entry) => entry.provider === selected) ?? [...runs].sort((a, b) => Number(b.run.status === 'running') - Number(a.run.status === 'running') || (b.run.started_at ?? '').localeCompare(a.run.started_at ?? ''))[0];
  const run = current?.run;
  const queued = entries.some((entry) => entry.queued);
  const running = active || queued || runs.some((entry) => entry.run.status === 'running');
  const agentName = publicAgentName(run?.agent) ?? providers.map((provider) => names[provider] ?? provider).join(' · ');
  const statusLabel = run?.pending_question ? 'Ждёт ответа' : run ? statuses[run.status] : queued ? 'В очереди' : active ? 'Запускается' : 'Нет активной работы';
  const statusColor = run?.status === 'failed' ? 'red' : run?.status === 'blocked' || run?.pending_question ? 'yellow' : (run ? run.status === 'running' : running) ? 'purple' : 'gray';
  const lastActivity = run?.last_activity_at ?? run?.events?.at(-1)?.at ?? run?.started_at;
  const serverTime = Date.parse(current?.server_time);
  const elapsed = current?.receivedAt == null ? 0 : Math.max(0, performance.now() - current.receivedAt);
  const quietMinutes = lastActivity && Number.isFinite(serverTime) ? Math.max(0, Math.floor((serverTime + elapsed - Date.parse(lastActivity)) / 60000)) : 0;
  const browserUsed = current?.available && run?.status === 'running' && (run.browser_used || run.events?.some((event) => /^(Входит через сохранённый вход|Читает (текущую )?страницу|Переходит по выбранной ссылке|Скачивает документ из браузера|Ищет в Google|Смотрит снимок браузера|Перемещает курсор|Нажимает|Вводит текст|Прокручивает страницу|Открывает страницу|Возвращается назад|Переходит вперёд|Рассматривает изображение|Ждёт, пока владелец завершит CAPTCHA)/.test(event.message)));
  const ameba = current?.provider === 'ameba' || !current && taskId === 0 && providers.includes('ameba');
  const livePlan = ameba && (run?.status === 'running' || run?.status === 'completed' && run.started_at > (ranking.data?.generated_at ?? ''));
  const tasks = livePlan ? run.timeline : ranking.data?.timeline;
  const plan = livePlan ? run.schedule : ranking.data?.plan_reset ? null : ranking.data?.schedule;
  const schedule = plan ?? ranking.data?.calendar_schedule;
  const hasTimeline = ameba && (schedule?.blocks?.length > 0 || tasks?.some((task) => task.plan));
  if (!providers.length) return null;
  return <Paper withBorder p="sm" radius="md" className="agent-progress">
    <Group justify="space-between" align="center" wrap="wrap">
      <Group gap="xs">
        {runs.length > 1 ? <Select size="xs" aria-label="Журнал агента" data={runs.map((entry) => ({ value: entry.provider, label: publicAgentName(entry.run.agent) ?? names[entry.provider] }))} value={current.provider} allowDeselect={false} onChange={(value) => { setSelected(value); client.setQueryData(selectionKey, value); }} /> : <Text size="sm" fw={600}>{agentName}</Text>}
        <Badge color={statusColor} variant="light">{statusLabel}</Badge>
      </Group>
      <Button variant="subtle" size="xs" className="agent-log-toggle" aria-expanded={opened} leftSection={<Loader size="xs" style={{ visibility: running && !run?.pending_question ? 'visible' : 'hidden' }} />} onClick={() => { client.setQueryData(openKey, !opened); setOpened(!opened); }}>Журнал {opened ? '▴' : '▾'}</Button>
    </Group>
    {run?.status === 'running' && <div className="agent-activity" key={run.id}>
      <Group gap="xs" align="start" role="status" aria-live="polite">
        {run.pending_question ? <Text size="sm">Ждёт вашего ответа</Text> : run.model_waiting ? <Text component="span" className="agent-thinking">Думает</Text> : <Text size="sm"><AgentExplanation text={run.current_action ?? 'Выполняет текущий шаг'} /></Text>}
      </Group>
      {run.agent_comment && <Text size="xs" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>Пояснение агента: <AgentExplanation text={run.agent_comment} /></Text>}
    </div>}
    {run?.status === 'failed' && <Alert color="red">{run.error || 'Агент завершился с ошибкой. Повторите поручение.'}</Alert>}
    {entries.filter((entry) => entry.run?.pending_question).map((entry) => <AgentQuestion key={entry.run.pending_question.id} question={entry.run.pending_question} agent={entry.run.agent} />)}
    {opened && <div className={`agent-log-body ${browserUsed || hasTimeline ? 'has-visual' : ''}`}>
      {(browserUsed || hasTimeline) && <aside className="agent-visual">
        {browserUsed && <MonocleBrowser key={run.id} active />}
        {hasTimeline && <Stack gap={4}>
          <Button size="compact-xs" variant="subtle" onClick={() => setExpanded(true)}>Увеличить таймлайн ↗</Button>
          <AgentTimeline tasks={tasks} schedule={schedule} compact calendarOnly={!plan} finished={run?.status !== 'running'} />
        </Stack>}
      </aside>}
      <div className="agent-log-content">
        <Text size="xs" c="dimmed">{run?.token_usage ? `Подтверждённые токены: вход ${run.token_usage.input_tokens ?? 'нет данных'} · выход ${run.token_usage.output_tokens ?? 'нет данных'}` : 'Токены: нет данных'}</Text>
        {current?.available && !streamError && run?.status === 'running' && !run.pending_question && quietMinutes >= 2 && <Text size="xs" c="yellow">Агент не передавал новых данных {quietMinutes} мин.</Text>}
        {run ? <AgentLog key={run.id} run={run} provider={current.provider} /> : <Text size="xs" c="dimmed">{running ? 'Ожидаем события агента…' : 'Журналов пока нет'}</Text>}
      </div>
    </div>}
    {entries.filter((entry) => !entry.available).map((entry) => <Text key={entry.provider} size="xs" c="yellow">{names[entry.provider]}: журнал пока недоступен</Text>)}
    {streamError && <Alert color="yellow">Соединение с журналом потеряно. Переподключаемся…</Alert>}
    <Modal opened={expanded && hasTimeline} onClose={() => setExpanded(false)} title="План Ameba" size="90vw" fullScreen={compact} centered>
      {hasTimeline && <AgentTimeline tasks={tasks} schedule={schedule} calendarOnly={!plan} finished={run?.status !== 'running'} />}
    </Modal>
  </Paper>;
}
