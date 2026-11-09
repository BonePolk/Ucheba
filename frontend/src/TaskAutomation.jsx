import { useState } from 'react';
import { Alert, Anchor, Badge, Button, Group, Paper, Stack, Text, TextInput, Textarea, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';
import ContextFiles from './ContextFiles';
import ContextBrowser from './ContextBrowser';

function RunFiles({ run }) {
  const files = useQuery({ queryKey: ['integrations', 'files', run.id], queryFn: () => request(`/integrations/codex/runs/${run.id}/files`), enabled: run.status === 'succeeded' });
  return <Stack gap="xs">{files.data?.map((name) => <Anchor key={name} href={`/api/integrations/codex/runs/${run.id}/files/${name.split('/').map(encodeURIComponent).join('/')}`} download>{name}</Anchor>)}{files.error && <Text c="red">{files.error.message}</Text>}</Stack>;
}

function ContextForm({ taskId, jevEnabled }) {
  const client = useQueryClient();
  const [subject, setSubject] = useState(null);
  const [answer, setAnswer] = useState(null);
  const context = useQuery({ queryKey: ['context', taskId], queryFn: () => request(`/integrations/tasks/${taskId}/context`), refetchInterval: 5000 });
  const analyze = useMutation({
    mutationFn: () => request(`/integrations/tasks/${taskId}/context/analyze`, { method: 'POST' }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['context', taskId] });
      await client.invalidateQueries({ queryKey: ['tasks'] });
    },
    onSettled: () => client.invalidateQueries({ queryKey: ['context-files', taskId] }),
  });
  const save = useMutation({
    mutationFn: () => request(`/integrations/tasks/${taskId}/context`, { method: 'POST', body: JSON.stringify({ subject: subject ?? context.data?.subject ?? '', answer: answer ?? context.data?.owner_notes ?? '' }) }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['context', taskId] });
      await client.invalidateQueries({ queryKey: ['tasks'] });
    },
    onSettled: () => client.invalidateQueries({ queryKey: ['context-files', taskId] }),
  });
  return <Paper withBorder p="md"><Stack gap="sm">
    <Title order={5}>Контекст · GPT-6 Luna</Title>
    <Text size="sm">Luna проверяет контекст перед Jev. Укажи один и тот же предмет для связанных задач: ответы сохраняются в памяти предмета и используются в следующих оценках.</Text>
    <ContextFiles taskId={taskId} disabled={analyze.isPending || save.isPending} />
    <ContextBrowser taskId={taskId} disabled={analyze.isPending || save.isPending} />
    <Button variant="light" loading={analyze.isPending} disabled={save.isPending} onClick={() => analyze.mutate()}>Проверить контекст через Luna</Button>
    {analyze.isSuccess && <Text size="sm">{analyze.data.status === 'ready' ? 'Контекста достаточно для оценки Jev.' : 'Luna запросила уточнения — ответь на вопросы ниже.'}</Text>}
    {context.data?.usage && <Text size="xs" c="dimmed">Последняя проверка: вход {context.data.usage.input_tokens ?? 0}, выход {context.data.usage.output_tokens ?? 0} токенов</Text>}
    {context.data?.questions?.length > 0 && <Alert color="yellow"><Stack gap="xs">{context.data.questions.map((question, i) => <Text key={i} size="sm">{question}</Text>)}</Stack></Alert>}
    {context.data?.summary && <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>{context.data.summary}</Text>}
    {context.data?.usage?.sources?.length > 0 && <Stack gap="xs"><Text size="sm" fw={500}>Использованные страницы</Text>{context.data.usage.sources.map((source) => <Anchor key={source.url} href={source.url} target="_blank" rel="noreferrer" size="sm">{source.title}</Anchor>)}</Stack>}
    <TextInput label="Предмет / область контекста" placeholder="Например: операционные системы" value={subject ?? context.data?.subject ?? ''} onChange={(event) => setSubject(event.currentTarget.value)} />
    <Textarea label="Ответы и известные факты" description="Сохрани полезные прошлые ответы; допиши новые уточнения. Данные отправляются в настроенный шлюз Luna." autosize minRows={3} value={answer ?? context.data?.owner_notes ?? ''} onChange={(event) => setAnswer(event.currentTarget.value)} />
    <Button loading={save.isPending} disabled={analyze.isPending || !(answer ?? context.data?.owner_notes ?? '').trim()} onClick={() => save.mutate()}>{jevEnabled ? 'Сохранить контекст и обновить порядок' : 'Сохранить контекст'}</Button>
    {save.data?.metrics?.status === 'needs_context' && <Text c="yellow">Нужны дополнительные уточнения — см. вопросы выше.</Text>}
    {save.data?.rank != null && <Text>Порядок Jev: {save.data.rank} из {save.data.ranked_count}</Text>}
    {(save.error || analyze.error || context.error) && <Alert color="red">{(save.error || analyze.error || context.error).message}</Alert>}
  </Stack></Paper>;
}

export default function TaskAutomation({ task, features = {} }) {
  const client = useQueryClient();
  const [notice, setNotice] = useState('');
  const runs = useQuery({ queryKey: ['integrations', 'runs', task.id], queryFn: () => request(`/integrations/tasks/${task.id}/runs`), refetchInterval: 5000, retry: false, enabled: features.codex !== false });
  const action = useMutation({
    mutationFn: (provider) => request(`/integrations/tasks/${task.id}/${provider}`, { method: 'POST' }),
    onSuccess: async (data, provider) => {
      setNotice(provider === 'codex' ? 'Задача передана Codex' : data.metrics?.status === 'needs_context' ? `Jev: недостаточно контекста. ${data.metrics.reason}` : data.rank != null ? `Порядок Jev: ${data.rank} из ${data.ranked_count}` : 'Порядок Jev обновлён');
      await client.invalidateQueries({ queryKey: ['jev-ranking'] });
      await client.invalidateQueries({ queryKey: ['tasks'] });
      await client.invalidateQueries({ queryKey: ['context', task.id] });
      await client.invalidateQueries({ queryKey: ['context-files', task.id] });
      await client.invalidateQueries({ queryKey: ['integrations', 'runs', task.id] });
    },
  });
  return <Stack>
    <Text fw={500}>{task.title}</Text>
    <Text size="sm" c="dimmed">Сначала подключите провайдеров в разделе «Интеграции». Задача уже сохранена и не зависит от доступности ИИ.</Text>
    <Group>{features.codex !== false && <Button disabled={action.isPending} onClick={() => action.mutate('codex')}>Поручить Codex</Button>}{features.jev !== false && features.context !== false && <Button variant="light" disabled={action.isPending} onClick={() => action.mutate('jev')}>Обновить порядок Jev</Button>}</Group>
    {features.context !== false && <ContextForm key={task.id} taskId={task.id} jevEnabled={features.jev !== false} />}
    {notice && <Alert color="purple">{notice}</Alert>}
    {(action.error || runs.error) && <Alert color="red">{(action.error || runs.error).message}</Alert>}
    {features.codex !== false && runs.data?.map((run) => <Paper key={run.id} withBorder p="md"><Stack gap="sm">
      <Group justify="space-between"><Title order={5}>Материалы Codex</Title><Badge>{({ queued: 'В очереди', running: 'Подготовка', succeeded: 'На проверку', failed: 'Ошибка', interrupted: 'Прервано' })[run.status]}</Badge></Group>
      {run.summary && <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>{run.summary}</Text>}
      {run.error && <Text c="red" size="sm">{run.error}</Text>}
      <RunFiles run={run} />
    </Stack></Paper>)}
  </Stack>;
}
