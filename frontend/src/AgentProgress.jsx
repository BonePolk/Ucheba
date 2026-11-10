import { useEffect, useRef, useState } from 'react';
import { Alert, Badge, Button, Collapse, Group, Loader, Paper, Stack, Text } from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';

const names = { context: 'Lucheba', jev: 'Ameba', codex: 'Codex' };
const statuses = { running: 'Работает', completed: 'Завершено', failed: 'Ошибка', interrupted: 'Прервано' };

export default function AgentProgress({ taskId = 0, providers, active = false, children }) {
  const client = useQueryClient();
  const providerList = providers.join(',');
  const openKey = ['agent-progress-open', taskId, providerList];
  const [opened, setOpened] = useState(() => client.getQueryData(openKey) ?? true);
  const viewport = useRef(null);
  function toggle() {
    client.setQueryData(openKey, !opened);
    setOpened(!opened);
  }
  const query = useQuery({
    queryKey: ['agent-progress', taskId, providerList],
    queryFn: async () => {
      const entries = await request(`/integrations/progress?task_id=${taskId}&providers=${providerList}`);
      const previous = client.getQueryData(['agent-progress', taskId, providerList]) ?? [];
      // Временная недоступность сервиса не стирает уже полученный журнал.
      const result = entries.map((entry) => ({ ...entry, run: entry.run ?? previous.find((item) => item.provider === entry.provider)?.run ?? null }));
      return result;
    },
    enabled: providers.length > 0,
    gcTime: Infinity,
    retry: false,
    refetchInterval: (query) => active || opened || query.state.data?.some((entry) => entry.queued || entry.run?.status === 'running') ? 1000 : false,
  });
  useEffect(() => {
    if (active) {
      client.setQueryData(['agent-progress-open', taskId, providerList], true);
      setOpened(true);
    }
  }, [active, client, taskId, providerList]);
  useEffect(() => {
    if (active || opened) query.refetch();
  }, [active, opened, query.refetch]);
  useEffect(() => {
    if (viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
  }, [query.data, opened]);
  const runs = (query.data ?? []).filter((entry) => entry.run);
  const queued = (query.data ?? []).some((entry) => entry.queued);
  const running = active || queued || runs.some((entry) => entry.run.status === 'running');
  const current = [...runs].sort((a, b) =>
    Number(b.run.status === 'running') - Number(a.run.status === 'running') ||
    (b.run.started_at ?? '').localeCompare(a.run.started_at ?? ''))[0];
  const events = (current?.run.events ?? []).map((event) => ({ ...event,
    provider: current.provider, key: `${current.run.id}:${event.id}` }));
  if (!providers.length) return null;
  return <Paper withBorder p="sm" radius="md">
    {queued && <Text size="xs" c="dimmed">Следующий запрос ожидает в очереди</Text>}
    <Button variant="subtle" size="xs" leftSection={running ? <Loader size="xs" /> : null} onClick={toggle}>
      Работа агента
    </Button>
    {typeof children === 'function' ? children(query.data ?? []) : children}
    <Collapse in={opened}><Stack gap="xs" mt="xs">
      <Group gap="xs">{(current ? [current] : []).map(({ provider, run }) => <Badge key={provider} color={run.status === 'failed' ? 'red' : run.status === 'running' ? 'purple' : 'gray'} variant="light">{run.agent ?? names[provider]} · {statuses[run.status]}</Badge>)}</Group>
      {events.length === 0 && <Text size="xs" c="dimmed">{running ? 'Ожидаем события агента…' : 'Журналов пока нет'}</Text>}
      <div ref={viewport} role="log" aria-live="polite" style={{ maxHeight: 280, overflowY: 'auto' }}>
        {events.map((event) => <Text key={event.key} size="xs" style={{ fontFamily: 'monospace', overflowWrap: 'anywhere', padding: '3px 0', whiteSpace: 'pre-wrap' }}>
          <Text component="span" inherit c="dimmed">{new Date(event.at).toLocaleTimeString()} · {current?.run.agent ?? names[event.provider]}{event.task_id ? ` #${event.task_id}` : ''} · </Text>{event.message}
        </Text>)}
      </div>
      {(query.data ?? []).filter((entry) => !entry.available).map((entry) => <Text key={entry.provider} size="xs" c="yellow">{names[entry.provider]}: журнал пока недоступен</Text>)}
      {query.error && <Alert color="red">{query.error.message}</Alert>}
    </Stack></Collapse>
  </Paper>;
}
