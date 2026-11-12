import { useState } from 'react';
import { Alert, Badge, Button, Group, Paper, Stack, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { request, agentName } from './api';

const statuses = { running: 'В работе', completed: 'Завершено', interrupted: 'Прервано' };

export default function AgentHistory() {
  const [before, setBefore] = useState(null);
  const history = useQuery({
    queryKey: ['agent-history', before],
    queryFn: () => request(`/auth/agent-history${before ? `?before=${before}` : ''}`),
    refetchInterval: 10000,
  });
  const items = history.data ?? [];
  return <Paper withBorder p="md"><Stack gap="xs">
    <Group justify="space-between"><Title order={4}>История агентов</Title><Button size="xs" variant="subtle" onClick={() => { setBefore(null); history.refetch(); }}>Обновить</Button></Group>
    <Text size="xs" c="dimmed">Arrodes, Straus, Monocle и Ameba используют недавние поручения и результаты из этой истории. Она сохраняется между запусками.</Text>
    {history.error && <Alert color="red">{history.error.message}</Alert>}
    {!items.length && <Text c="dimmed" role="status">{history.isPending ? 'Загрузка истории…' : 'История пока пуста.'}</Text>}
    {items.map((item) => <Paper key={item.id} withBorder p="xs"><details>
      <summary style={{ cursor: 'pointer' }}>
        <Group gap="xs" component="span"><Text component="span" size="sm" fw={600}>{agentName(item.agent)}</Text><Badge size="xs" variant="light">{statuses[item.status] ?? item.status}</Badge><Text component="span" size="xs" c="dimmed">{new Date(item.created_at).toLocaleString()}</Text></Group>
        <Text component="span" display="block" size="sm" lineClamp={1}>{item.request.instruction || item.request.task?.title || item.request.subject || (item.request.task_id ? `Задача №${item.request.task_id}` : 'Сортировка задач')}</Text>
      </summary>
      <Text size="sm" c="dimmed" mt="xs" style={{ whiteSpace: 'pre-wrap' }}>{item.result.reason || item.result.explanation || item.result.summary}</Text>
      <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: 12 }}>{JSON.stringify({ request: item.request, result: item.result }, null, 2)}</pre>
    </details></Paper>)}
    <Group>{before && <Button size="xs" variant="light" onClick={() => setBefore(null)}>Последние записи</Button>}{items.length === 20 && <Button size="xs" variant="light" onClick={() => setBefore(items.at(-1).id)}>Более ранние</Button>}</Group>
  </Stack></Paper>;
}
