import AgentExplanation from './AgentExplanation';
import { useEffect, useRef, useState } from 'react';
import { Alert, Badge, Button, Collapse, Group, Loader, Paper, Stack, Text } from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';

const names = { monocle: 'Monocle', straus: 'Straus', context: 'Monocle', jev: 'Ameba', codex: 'Codex' };
const statuses = { running: 'Работает', completed: 'Завершено', failed: 'Ошибка', interrupted: 'Прервано', blocked: 'Нужны данные или доступ', unchanged: 'Без изменений' };

export default function AgentProgress({ taskId = 0, providers, active = false, children }) {
  const client = useQueryClient();
  const providerList = providers.join(',');
  const openKey = ['agent-progress-open', taskId, providerList];
  const [opened, setOpened] = useState(() => client.getQueryData(openKey) ?? true);
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
        client.setQueryData(key, (previous = []) => entries.map((entry) => ({
          ...entry,
          // Обрыв сервиса не стирает журнал, который уже успел прийти.
          run: entry.run ?? previous.find((item) => item.provider === entry.provider)?.run ?? null,
        })));
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
  const lastActivity = run?.last_activity_at ?? run?.events?.at(-1)?.at ?? run?.started_at;
  const quietMinutes = lastActivity ? Math.floor((Date.now() - Date.parse(lastActivity)) / 60000) : 0;
  const allEvents = current?.run.events ?? [];
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
  return <Paper withBorder p="sm" radius="md">
    {queued && <Text size="xs" c="dimmed">Следующий запрос ожидает в очереди</Text>}
    <Button variant="subtle" size="xs" leftSection={running ? <Loader size="xs" /> : null} onClick={toggle}>
      Работа агента
    </Button>
    {run?.status === 'running' && <Group gap="xs" align="start" role="status" aria-live="polite">
      {run.model_waiting ? <Text component="span" className="agent-thinking">Думает</Text> : <>
        <Loader size={12} mt={3} />
        <Stack gap={2} style={{ flex: 1, minWidth: 0 }}>
          <Text size="sm" fw={500} style={{ overflowWrap: 'anywhere' }}><AgentExplanation text={run.current_action ?? 'Выполняет текущий шаг'} initialText={seenExplanations.current.get(`${run.id}:action`) ?? (receivedLog.current ? '' : run.current_action)} /></Text>
        </Stack>
      </>}
    </Group>}
    {run?.agent_comment && <Text size="xs" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>Пояснение агента: <AgentExplanation text={run.agent_comment} initialText={seenExplanations.current.get(`${run.id}:comment`) ?? (receivedLog.current ? '' : run.agent_comment)} /></Text>}

    {typeof children === 'function' ? children(query.data ?? [], current) : children}
    <Collapse in={opened}><Stack gap="xs" mt="xs">
      <Group gap="xs">{(current ? [current] : []).map(({ provider, run }) => <Badge key={provider} color={run.status === 'failed' ? 'red' : run.status === 'blocked' ? 'yellow' : run.status === 'running' ? 'purple' : 'gray'} variant="light">{(run.agent === 'Lucheba' ? 'Monocle' : run.agent) ?? names[provider]} · {statuses[run.status]}</Badge>)}</Group>
      <Text size="xs" c="dimmed">{run?.token_usage ? `Подтверждённые токены: вход ${run.token_usage.input_tokens ?? 'нет данных'} · выход ${run.token_usage.output_tokens ?? 'нет данных'}` : 'Токены: нет данных'}{run?.model_waiting && !run.current_usage_known && run.token_usage ? ' · текущий запрос: нет данных' : ''}</Text>
      {run?.status === 'running' && quietMinutes >= 2 && <Text size="xs" c="yellow">Новых данных нет {quietMinutes} мин. Операция ещё не завершена.</Text>}
      {events.length === 0 && <Text size="xs" c="dimmed">{running ? 'Ожидаем события агента…' : 'Журналов пока нет'}</Text>}
      <div ref={viewport} onScroll={(event) => {
        const element = event.currentTarget;
        followLog.current = element.scrollHeight - element.scrollTop - element.clientHeight < 32;
      }} role="log" aria-live="polite" style={{ maxHeight: 280, overflowY: 'auto' }}>
        <div ref={logContent}>
        {events.map((event, index) => {
          const initialText = seenExplanations.current.get(event.key) ?? (receivedLog.current ? '' : event.message);
          return <div key={event.key} style={{ padding: '4px 0', overflowWrap: 'anywhere' }}>
            <Text size="xs" style={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
              <Text component="span" inherit c="dimmed">{new Date(event.at).toLocaleTimeString()} · {current?.run.agent === 'Lucheba' ? 'Monocle' : current?.run.agent ?? names[event.provider]}{event.task_id ? ` #${event.task_id}` : ''} · </Text>
              <AgentExplanation text={event.message} initialText={initialText} animate={index === events.length - 1} />
            </Text>

          </div>;
        })}
        </div>
      </div>
      {(query.data ?? []).filter((entry) => !entry.available).map((entry) => <Text key={entry.provider} size="xs" c="yellow">{names[entry.provider]}: журнал пока недоступен</Text>)}
      {streamError && <Alert color="yellow">Соединение с журналом потеряно. Переподключаемся…</Alert>}
    </Stack></Collapse>
  </Paper>;
}
