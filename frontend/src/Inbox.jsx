import { useState } from 'react';
import { Alert, Anchor, Badge, Button, Group, Loader, Paper, Select, Stack, Text, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import dayjs from 'dayjs';
import { getTasks, request } from './api';
import TaskForm from './TaskForm';

export default function Inbox() {
  const client = useQueryClient();
  const [editing, setEditing] = useState(null);
  const [targets, setTargets] = useState({});
  const query = useQuery({ queryKey: ['inbox'], queryFn: () => request('/inbox'), refetchInterval: 30000, retry: false });
  const tasks = useQuery({ queryKey: ['tasks', 'inbox-targets'], queryFn: () => getTasks() });
  const decision = useMutation({
    mutationFn: ({ id, action, values, target_task_id }) => request(`/inbox/${id}/${action}`, { method: 'POST', body: JSON.stringify({ values, target_task_id }) }),
    onSuccess: async () => {
      setEditing(null);
      await client.invalidateQueries({ queryKey: ['inbox'] });
      await client.invalidateQueries({ queryKey: ['tasks'] });
    },
  });
  const accept = (item, values) => decision.mutate({ id: item.id, action: 'accept', values, target_task_id: targets[item.id] ? Number(targets[item.id]) : item.target_task_id });
  if (query.isPending) return <Loader />;
  return <Stack>
    <Title order={2}>На подтверждение</Title>
    {(query.error || decision.error) && <Alert color="red">{(query.error || decision.error).message}</Alert>}
    {query.data?.length === 0 && <Text c="dimmed">Новых предложений нет</Text>}
    {query.data?.map((item) => <Paper key={item.id} withBorder p="lg"><Stack gap="sm">
      <Group><Title order={4}>{item.title}</Title><Badge>{({ create: 'Новая задача', update: 'Изменение', cancel: 'Отмена' })[item.action]}</Badge></Group>
      {item.description && <Text style={{ whiteSpace: 'pre-wrap' }}>{item.description}</Text>}
      <Text size="sm">Дедлайн: {item.deadline ? dayjs.utc(item.deadline).local().format('DD.MM.YYYY HH:mm') : 'не определён'}</Text>
      <Text size="sm">Источник: {item.source_title} · Получатель: {item.assignee || 'не определён'}</Text>
      {item.source_url && <Anchor href={item.source_url} target="_blank" rel="noreferrer">Открыть сообщение в Telegram</Anchor>}
      <Text size="sm" c="dimmed">{item.reason}</Text>
      {item.action !== 'create' && <Select label="Изменяемая задача" searchable clearable value={targets[item.id] ?? (item.target_task_id ? String(item.target_task_id) : null)} onChange={(value) => setTargets({ ...targets, [item.id]: value })} data={(tasks.data ?? []).map((task) => ({ value: String(task.id), label: `#${task.id} ${task.title}` }))} />}
      <Group><Button disabled={decision.isPending} onClick={() => accept(item)}>Принять</Button>{item.action !== 'cancel' && <Button variant="default" onClick={() => { decision.reset(); setEditing(item); }}>Редактировать</Button>}<Button variant="subtle" color="red" disabled={decision.isPending} onClick={() => decision.mutate({ id: item.id, action: 'reject' })}>Отклонить</Button></Group>
    </Stack></Paper>)}
    {editing && <TaskForm task={{ ...editing, id: undefined }} onClose={() => setEditing(null)} onSubmit={(values) => accept(editing, values)} pending={decision.isPending} error={decision.error} />}
  </Stack>;
}
