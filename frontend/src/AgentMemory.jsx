import { useState } from 'react';
import { Alert, Button, Group, Paper, Stack, Text, Textarea, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';

export default function AgentMemory() {
  const client = useQueryClient();
  const [editing, setEditing] = useState(null);
  const [adding, setAdding] = useState(false);
  const [newFact, setNewFact] = useState('');
  const memory = useQuery({ queryKey: ['agent-memory'], queryFn: () => request('/auth/agent-memory'), refetchInterval: 10000 });
  const save = useMutation({
    mutationFn: () => request('/auth/agent-memory', { method: 'PUT', body: JSON.stringify(editing) }),
    onSuccess: () => { setEditing(null); client.invalidateQueries({ queryKey: ['agent-memory'] }); client.invalidateQueries({ queryKey: ['jev-ranking'] }); },
  });
  const create = useMutation({
    mutationFn: (fact) => request('/auth/agent-memory', { method: 'POST', body: JSON.stringify(fact) }),
    onSuccess: () => { setNewFact(''); setAdding(false); client.invalidateQueries({ queryKey: ['agent-memory'] }); client.invalidateQueries({ queryKey: ['jev-ranking'] }); },
  });
  const remove = useMutation({
    mutationFn: (key) => request(`/auth/agent-memory/${encodeURIComponent(key)}`, { method: 'DELETE' }),
    onSuccess: () => { client.invalidateQueries({ queryKey: ['agent-memory'] }); client.invalidateQueries({ queryKey: ['jev-ranking'] }); },
  });
  return <Paper withBorder p="md"><Stack gap="xs">
    <Group justify="space-between"><Title order={4}>Память агентов</Title><Button size="compact-xs" variant="light" onClick={() => setAdding(!adding)}>Добавить факт</Button></Group>
    {adding && <Stack gap="xs"><Textarea label="Факт обо мне" placeholder="Например: не хочу просыпаться рано, утром мне тяжело сосредоточиться" value={newFact} onChange={(event) => setNewFact(event.currentTarget.value)} autosize minRows={2} maxLength={1500} /><Group gap="xs"><Button size="compact-xs" loading={create.isPending} disabled={!newFact.trim()} onClick={() => create.mutate({ text: newFact.trim() })}>Сохранить факт</Button><Button size="compact-xs" variant="subtle" onClick={() => setAdding(false)}>Отмена</Button></Group></Stack>}
    {(memory.error || save.error || remove.error || create.error) && <Alert color="red">{(memory.error || save.error || remove.error || create.error).message}</Alert>}
    {!memory.isPending && !memory.data?.length && <Text size="sm" c="dimmed">Сохранённых фактов пока нет.</Text>}
    {(memory.data ?? []).map((fact) => <Paper key={fact.key} withBorder p="xs">
      {editing?.key === fact.key ? <Stack gap="xs">
        <Textarea value={editing.text} onChange={(event) => setEditing({ ...editing, text: event.currentTarget.value })} autosize maxLength={1500} />
        <Group gap="xs"><Button size="compact-xs" loading={save.isPending} disabled={!editing.text.trim()} onClick={() => save.mutate()}>Сохранить</Button><Button size="compact-xs" variant="subtle" onClick={() => setEditing(null)}>Отмена</Button></Group>
      </Stack> : <Group justify="space-between" align="start" className="memory-fact">
        <div style={{ flex: 1, minWidth: 0 }}><Text size="sm" style={{ overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>{fact.text}</Text><Text size="xs" c="dimmed">{fact.agent === 'Lucheba' ? 'Monocle' : fact.agent} · {new Date(fact.updated_at).toLocaleDateString()}</Text></div>
        <Group gap={4} wrap="nowrap"><Button size="compact-xs" variant="subtle" onClick={() => setEditing({ key: fact.key, text: fact.text })}>Изменить</Button><Button size="compact-xs" variant="subtle" color="red" loading={remove.isPending && remove.variables === fact.key} onClick={() => remove.mutate(fact.key)}>Забыть</Button></Group>
      </Group>}
    </Paper>)}
  </Stack></Paper>;
}
