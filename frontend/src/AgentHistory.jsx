import { useState } from 'react';
import { Alert, Badge, Button, Group, Paper, Stack, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { request } from './api';

const statuses = { running: 'В работе', completed: 'Завершено', interrupted: 'Прервано' };

export default function AgentHistory() {
  const [before, setBefore] = useState(null);
  const history = useQuery({
    queryKey: ['agent-history', before],
    queryFn: () => request(`/auth/agent-history${before ? `?before=${before}` : ''}`),
    refetchInterval: 10000,
  });
  const items = history.data ?? [];
  return <Paper withBorder p="lg"><Stack gap="sm">
    <Group justify="space-between"><Title order={3}>Общая история агентов</Title><Button size="xs" variant="subtle" onClick={() => { setBefore(null); history.refetch(); }}>Обновить</Button></Group>
    <Text size="xs" c="dimmed">Straus, Lucheba и Ameba используют недавние поручения и результаты из этой истории. Она сохраняется между запусками.</Text>
    {history.error && <Alert color="red">{history.error.message}</Alert>}
    {!history.isPending && !items.length && <Text c="dimmed">История пока пуста.</Text>}
    {items.map((item) => <Paper key={item.id} withBorder p="sm">
      <Group gap="xs"><Text fw={600}>{item.agent}</Text><Badge size="xs" variant="light">{statuses[item.status] ?? item.status}</Badge><Text size="xs" c="dimmed">{new Date(item.created_at).toLocaleString()}</Text></Group>
      <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>{item.request.instruction || item.request.task?.title || item.request.subject || (item.request.task_id ? `Задача №${item.request.task_id}` : 'Сортировка задач')}</Text>
      <Text size="sm" c="dimmed" style={{ whiteSpace: 'pre-wrap' }}>{item.result.reason || item.result.explanation || item.result.summary}</Text>
      <details><summary>Полная запись</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: 12 }}>{JSON.stringify({ request: item.request, result: item.result }, null, 2)}</pre></details>
    </Paper>)}
    <Group>{before && <Button size="xs" variant="light" onClick={() => setBefore(null)}>Последние записи</Button>}{items.length === 20 && <Button size="xs" variant="light" onClick={() => setBefore(items.at(-1).id)}>Более ранние</Button>}</Group>
  </Stack></Paper>;
}
