import { useEffect, useRef, useState } from 'react';
import { Alert, Badge, Button, Collapse, Group, Loader, Paper, Stack, Text } from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';

const names = { context: 'Luna', codex: 'Codex' };
const statuses = { running: 'Работает', completed: 'Завершено', failed: 'Ошибка', interrupted: 'Прервано' };

export default function AgentProgress({ taskId = 0, providers, active = false }) {
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
    queryFn: () => request(`/integrations/progress?task_id=${taskId}&providers=${providerList}`),
    enabled: providers.length > 0,
    gcTime: Infinity,
    retry: false,
    refetchInterval: (query) => active || query.state.data?.some((entry) => entry.run?.status === 'running') ? 1000 : false,
  });
  useEffect(() => {
    if (active) {
      client.setQueryData(['agent-progress-open', taskId, providerList], true);
      setOpened(true);
    }
  }, [active, client, taskId, providerList]);
  useEffect(() => {
    if (!active && opened) query.refetch();
  }, [active, opened, query.refetch]);
  useEffect(() => {
    if (viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
  }, [query.data, opened]);
  const runs = (query.data ?? []).filter((entry) => entry.run);
  const running = active || runs.some((entry) => entry.run.status === 'running');
  const events = runs.flatMap(({ provider, run }) => run.events.map((event) => ({ ...event, provider, key: `${run.id}:${event.id}` })))
    .sort((a, b) => a.at.localeCompare(b.at));
  if (!providers.length) return null;
  return <Paper withBorder p="sm" radius="md">
    <Button variant="subtle" size="xs" leftSection={running ? <Loader size="xs" /> : null} onClick={toggle}>
      Ход работы: {providers.map((name) => names[name]).join(', ')}
    </Button>
    <Collapse in={opened}><Stack gap="xs" mt="xs">
      <Group gap="xs">{runs.map(({ provider, run }) => <Badge key={provider} color={run.status === 'failed' ? 'red' : run.status === 'running' ? 'purple' : 'gray'} variant="light">{names[provider]} · {statuses[run.status]}</Badge>)}</Group>
      {events.length === 0 && <Text size="xs" c="dimmed">{running ? 'Ожидаем события агента…' : 'Журналов пока нет'}</Text>}
      <div ref={viewport} role="log" aria-live="polite" style={{ maxHeight: 280, overflowY: 'auto' }}>
        {events.map((event) => <Text key={event.key} size="xs" style={{ fontFamily: 'monospace', overflowWrap: 'anywhere', padding: '3px 0', whiteSpace: 'pre-wrap' }}>
          <Text component="span" inherit c="dimmed">{new Date(event.at).toLocaleTimeString()} · {names[event.provider]} · </Text>{event.message}
        </Text>)}
      </div>
      {(query.data ?? []).filter((entry) => !entry.available).map((entry) => <Text key={entry.provider} size="xs" c="yellow">{names[entry.provider]}: журнал пока недоступен</Text>)}
      {query.error && <Alert color="red">{query.error.message}</Alert>}
    </Stack></Collapse>
  </Paper>;
}
