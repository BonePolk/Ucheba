import { useState } from 'react';
import { Alert, Anchor, Badge, Button, Group, Paper, Stack, Text, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';

function RunFiles({ run }) {
  const files = useQuery({ queryKey: ['integrations', 'files', run.id], queryFn: () => request(`/integrations/codex/runs/${run.id}/files`), enabled: run.status === 'succeeded' });
  return <Stack gap="xs">{files.data?.map((name) => <Anchor key={name} href={`/admin/api/integrations/codex/runs/${run.id}/files/${name.split('/').map(encodeURIComponent).join('/')}`} download>{name}</Anchor>)}{files.error && <Text c="red">{files.error.message}</Text>}</Stack>;
}

export default function TaskAutomation({ task }) {
  const client = useQueryClient();
  const [notice, setNotice] = useState('');
  const runs = useQuery({ queryKey: ['integrations', 'runs', task.id], queryFn: () => request(`/integrations/tasks/${task.id}/runs`), refetchInterval: 5000, retry: false });
  const action = useMutation({
    mutationFn: (provider) => request(`/integrations/tasks/${task.id}/${provider}`, { method: 'POST' }),
    onSuccess: async (data) => {
      setNotice(data.score !== undefined ? `Оценка Jev: ${data.score}/100` : 'Задача передана Codex');
      await client.invalidateQueries({ queryKey: ['tasks'] });
      await client.invalidateQueries({ queryKey: ['integrations', 'runs', task.id] });
    },
  });
  return <Stack>
    <Text fw={500}>{task.title}</Text>
    <Text size="sm" c="dimmed">Сначала подключите провайдеров в разделе «Интеграции». Задача уже сохранена и не зависит от доступности ИИ.</Text>
    <Group><Button disabled={action.isPending} onClick={() => action.mutate('codex')}>Поручить Codex</Button><Button variant="light" disabled={action.isPending} onClick={() => action.mutate('jev')}>Оценить через Jev</Button></Group>
    {notice && <Alert color="purple">{notice}</Alert>}
    {(action.error || runs.error) && <Alert color="red">{(action.error || runs.error).message}</Alert>}
    {runs.data?.map((run) => <Paper key={run.id} withBorder p="md"><Stack gap="sm">
      <Group justify="space-between"><Title order={5}>Материалы Codex</Title><Badge>{({ queued: 'В очереди', running: 'Подготовка', succeeded: 'На проверку', failed: 'Ошибка', interrupted: 'Прервано' })[run.status]}</Badge></Group>
      {run.summary && <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>{run.summary}</Text>}
      {run.error && <Text c="red" size="sm">{run.error}</Text>}
      <RunFiles run={run} />
    </Stack></Paper>)}
  </Stack>;
}
