import { agentName as publicAgentName } from './api';
import AgentExplanation from './AgentExplanation';
import AgentQuestion from './AgentQuestion';
import MonocleBrowser from './MonocleBrowser';
import { useEffect, useRef, useState } from 'react';
import { useMediaQuery } from '@mantine/hooks';
import { Alert, Badge, Button, Collapse, Group, Loader, Paper, Stack, Text } from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';

const names = { monocle: 'Monocle', straus: 'Straus', context: 'Monocle', ameba: 'Ameba', arrodes: 'Arrodes' };
const statuses = { running: 'Работает', completed: 'Завершено', failed: 'Ошибка', interrupted: 'Прервано', blocked: 'Нужны данные или доступ', unchanged: 'Без изменений' };

export default function AgentProgress({ taskId = 0, providers, active = false, children }) {
  const client = useQueryClient();
  const compact = useMediaQuery('(max-width: 767px)', window.innerWidth < 768);
  const providerList = providers.join(',');
  const openKey = ['agent-progress-open', taskId, providerList];
  const [opened, setOpened] = useState(() => new URLSearchParams(window.location.search).get('browser') === '1'
    || (client.getQueryData(openKey) ?? !compact));
  const viewport = useRef(null);
  const logContent = useRef(null);
  const followLog = useRef(true);
  const seenExplanations = useRef(new Map());
  const receivedLog = useRef(false);
  function toggle() {
    client.setQueryData(openKey, !opened);
    setOpened(!opened);
  }
  const [streamError, setStreamError] = useState(false);
  const query = useQuery({
    queryKey: ['agent-progress', taskId, providerList],
    queryFn: async () => [],
    initialData: [],
    enabled: false,
    gcTime: Infinity,
  });
  useEffect(() => {
    if (!providerList) return;
    const key = ['agent-progress', taskId, providerList];
    const stream = new EventSource(`/api/integrations/progress/stream?task_id=${taskId}&providers=${encodeURIComponent(providerList)}`);
    setStreamError(false);
    stream.onmessage = (event) => {
      try {
        const entries = JSON.parse(event.data);
        client.setQueryData(key, (previous = []) => entries.map((entry) => {
          const oldRun = previous.find((item) => item.provider === entry.provider)?.run;
          // Сохраняем журнал при обрыве связи. Доступный сервис без запуска
          // означает, что прежняя работа уже потеряна, например после перезапуска.
          const run = entry.run ?? (entry.available && oldRun?.status === 'running'
            ? { ...oldRun, status: 'interrupted', model_waiting: false, pending_question: null }
            : oldRun ?? null);
          return { ...entry, run, receivedAt: performance.now() };
        }));
        setStreamError(false);
      } catch {
        setStreamError(true);
      }
    };
    stream.onerror = () => setStreamError(true);
    // EventSource сам восстанавливает соединение; при уходе со страницы закрываем его.
    return () => stream.close();
  }, [client, taskId, providerList]);
  useEffect(() => {
    if (active) {
      client.setQueryData(['agent-progress-open', taskId, providerList], true);
      setOpened(true);
    }
  }, [active, client, taskId, providerList]);
  useEffect(() => {
    if (viewport.current && followLog.current) viewport.current.scrollTop = viewport.current.scrollHeight;
  }, [query.data, opened]);
  useEffect(() => {
    if (!logContent.current || !viewport.current) return;
    const observer = new ResizeObserver(() => {
      if (followLog.current && viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
    });
    observer.observe(logContent.current);
    return () => observer.disconnect();
  }, [opened]);
  const runs = (query.data ?? []).filter((entry) => entry.run);
  const queued = (query.data ?? []).some((entry) => entry.queued);
  const running = active || queued || runs.some((entry) => entry.run.status === 'running');
  const visibleRuns = active || queued ? runs.filter((entry) => entry.run.status === 'running') : runs;
  const current = [...visibleRuns].sort((a, b) =>
    Number(b.run.status === 'running') - Number(a.run.status === 'running') ||
    (b.run.started_at ?? '').localeCompare(a.run.started_at ?? ''))[0];
  const run = current?.run;
  const agentName = publicAgentName(run?.agent) ??
    (providers.length === 1 ? names[providers[0]] : providers.map((provider) => names[provider] ?? provider).join(' · '));
  const statusLabel = run?.pending_question ? 'Ждёт ответа' : run ? statuses[run.status] : queued ? 'В очереди' : active ? 'Запускается' : 'Нет активной работы';
  const statusColor = run?.status === 'failed' ? 'red' : run?.status === 'blocked' || run?.pending_question ? 'yellow' : running ? 'purple' : 'gray';
  const lastActivity = run?.last_activity_at ?? run?.events?.at(-1)?.at ?? run?.started_at;
  // Длительность считаем по серверным часам; часы пользователя могут отличаться.
  const serverTime = Date.parse(current?.server_time);
  const elapsed = current?.receivedAt == null ? 0 : Math.max(0, performance.now() - current.receivedAt);
  const quietMinutes = lastActivity && Number.isFinite(serverTime)
    ? Math.max(0, Math.floor((serverTime + elapsed - Date.parse(lastActivity)) / 60000)) : 0;
  const allEvents = current?.run.events ?? [];
  const browserUsed = current?.available && run?.status === 'running'
    && ['Monocle', 'Straus', 'Arrodes'].includes(publicAgentName(run.agent))
    && allEvents.some((event) => event.kind !== 'note'
      && /^(Входит через сохранённый вход|Читает (текущую )?страницу|Переходит по выбранной ссылке|Скачивает документ из браузера|Ищет в Google|Смотрит снимок браузера|Перемещает курсор|Нажимает|Вводит текст|Прокручивает страницу|Открывает страницу|Возвращается назад|Переходит вперёд|Рассматривает изображение|Ждёт, пока владелец завершит CAPTCHA)/.test(event.message));
  const events = allEvents.map((event) => ({
    ...event, provider: current.provider, key: `${current.run.id}:${event.id}`,
  }));
  // Первый снимок показываем сразу. Запоминаем текст, чтобы не анимировать его повторно.
  useEffect(() => {
    if (!runs.length) return;
    receivedLog.current = true;
    seenExplanations.current = new Map([
      ...events.map((event) => [event.key, event.message]),
      [`${run?.id}:action`, run?.current_action ?? ''],
      [`${run?.id}:comment`, run?.agent_comment ?? ''],
    ]);
  });
  if (!providers.length) return null;
  return <Paper withBorder p="sm" radius="md" className="agent-progress">
    <Group justify="space-between" align="center" wrap="wrap">
      <Group gap="xs"><Text size="sm" fw={600}>{agentName}</Text><Badge color={statusColor} variant="light">{statusLabel}</Badge></Group>
      <Button variant="subtle" size="xs" className="agent-log-toggle" aria-label={opened ? 'Скрыть журнал' : 'Показать журнал'} aria-expanded={opened} rightSection={<span aria-hidden="true">{opened ? '▴' : '▾'}</span>} leftSection={<Loader size="xs" style={{ visibility: running && !run?.pending_question ? 'visible' : 'hidden' }} />} onClick={toggle}>
        Журнал
      </Button>
    </Group>
    {(run?.status === 'running' || run?.agent_comment) && <div className="agent-activity">
    {run?.status === 'running' && <Group gap="xs" align="start" role="status" aria-live="polite">
      {run.pending_question ? <Text size="sm" fw={500}>Ждёт вашего ответа</Text> : run.model_waiting ? <Text component="span" className="agent-thinking">Думает</Text> : <>
        <Loader size={12} mt={3} />
        <Stack gap={2} style={{ flex: 1, minWidth: 0 }}>
          <Text size="sm" fw={500} style={{ overflowWrap: 'anywhere' }}><AgentExplanation text={run.current_action ?? 'Выполняет текущий шаг'} initialText={seenExplanations.current.get(`${run.id}:action`) ?? (receivedLog.current ? '' : run.current_action)} /></Text>
        </Stack>
      </>}
    </Group>}
    {run?.agent_comment && <Text size="xs" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>Пояснение агента: <AgentExplanation text={run.agent_comment} initialText={seenExplanations.current.get(`${run.id}:comment`) ?? (receivedLog.current ? '' : run.agent_comment)} /></Text>}
    </div>}
    {run?.status === 'failed' && <Alert color="red">{run.error || 'Агент завершился с ошибкой. Повторите поручение.'}</Alert>}
    {run?.pending_question && <AgentQuestion key={run.pending_question.id} question={run.pending_question} agent={run.agent} />}

    <Collapse in={opened}><Stack gap="xs" mt="xs">
      {typeof children === 'function' ? children(query.data ?? [], current) : children}
      <div className="agent-log-body">
      {opened && browserUsed && <MonocleBrowser key={run.id} active />}
      <div className="agent-log-content">
      <Text size="xs" c="dimmed">{run?.token_usage ? `Подтверждённые токены: вход ${run.token_usage.input_tokens ?? 'нет данных'} · выход ${run.token_usage.output_tokens ?? 'нет данных'}` : 'Токены: нет данных'}{run?.model_waiting && !run.current_usage_known && run.token_usage ? ' · текущий запрос: нет данных' : ''}</Text>
      {current?.available && !streamError && run?.status === 'running' && !run.pending_question && quietMinutes >= 2 && <Text size="xs" c="yellow">Агент не передавал новых данных {quietMinutes} мин.</Text>}
      <div ref={viewport} onScroll={(event) => {
        const element = event.currentTarget;
        followLog.current = element.scrollHeight - element.scrollTop - element.clientHeight < 32;
      }} role="log" aria-live="polite" className="agent-log">
        <div ref={logContent}>
        {events.length === 0 && <Text size="xs" c="dimmed">{running ? 'Ожидаем события агента…' : 'Журналов пока нет'}</Text>}
        {events.map((event, index) => {
          const initialText = seenExplanations.current.get(event.key) ?? (receivedLog.current ? '' : event.message);
          return <div key={event.key} style={{ padding: '4px 0', overflowWrap: 'anywhere' }}>
            <Text size="xs" style={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
              <Text component="span" inherit c="dimmed">{new Date(event.at).toLocaleTimeString()} · {publicAgentName(current?.run.agent) ?? names[event.provider]}{event.task_id ? ` #${event.task_id}` : ''} · </Text>
              <AgentExplanation text={event.message} initialText={initialText} animate={index === events.length - 1} />
            </Text>

          </div>;
        })}
        </div>
      </div>
      </div>
      </div>
      {(query.data ?? []).filter((entry) => !entry.available).map((entry) => <Text key={entry.provider} size="xs" c="yellow">{names[entry.provider]}: журнал пока недоступен</Text>)}
      {streamError && <Alert color="yellow">Соединение с журналом потеряно. Переподключаемся…</Alert>}
    </Stack></Collapse>
  </Paper>;
}
