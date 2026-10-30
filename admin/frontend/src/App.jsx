import { useState } from 'react';
import { Alert, Button, Container, Group, Loader, SegmentedControl, Stack, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import AdminGate from './AdminGate';
import Integrations from './Integrations';
import Inbox from './Inbox';
import TaskAutomation from './TaskAutomation';
import { request } from './api';

function TaskPanel({ id }) {
  const query = useQuery({ queryKey: ['tasks', id], queryFn: () => request(`/tasks/${id}`) });
  if (query.isPending) return <Loader />;
  if (query.error) return <Alert color="red">{query.error.message}</Alert>;
  return <TaskAutomation task={query.data} />;
}

export default function App() {
  const task = new URLSearchParams(window.location.search).get('task');
  const [view, setView] = useState(task ? 'task' : 'integrations');
  return <Container size="xl" py="xl"><Stack gap="lg">
    <Group justify="space-between"><Group><img src="/icon.png" alt="" width="44" height="44" /><Title order={1}>Админка</Title></Group><Button component="a" href="/" variant="default">Открыть задачи</Button></Group>
    <SegmentedControl value={view} onChange={setView} data={[{ value: 'integrations', label: 'Интеграции' }, { value: 'inbox', label: 'На подтверждение' }, ...(task ? [{ value: 'task', label: 'Действия с задачей' }] : [])]} />
    <AdminGate>{view === 'inbox' ? <Inbox /> : view === 'task' ? <TaskPanel id={task} /> : <Integrations />}</AdminGate>
  </Stack></Container>;
}
