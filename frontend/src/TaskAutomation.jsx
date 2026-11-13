import { useState } from 'react';
import { Alert, Anchor, Badge, Button, Group, Paper, Stack, Text, Textarea, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { agentRequest, request } from './api';
import ContextFiles from './ContextFiles';
import AgentProblemReport from './AgentProblemReport';
import ContextBrowser from './ContextBrowser';
import AgentProgress from './AgentProgress';
import RankingAssessment from './RankingAssessment';
import TaskMarkdown from './TaskMarkdown';

const runStatus = { queued: 'В очереди', running: 'Работает', succeeded: 'На проверку', failed: 'Ошибка', interrupted: 'Прервано' };

function RunFiles({ run }) {
  const [opened, setOpened] = useState(false);
  const files = useQuery({ queryKey: ['integrations', 'files', run.id], queryFn: () => request(`/integrations/arrodes/runs/${run.id}/files`), enabled: opened });
  return <details onToggle={(event) => setOpened(event.currentTarget.open)}>
    <summary style={{ cursor: 'pointer', fontSize: 13 }}>Файлы</summary>
    <Stack gap={4} mt="xs">{files.data?.map((name) => <Anchor size="sm" key={name} href={`/api/integrations/arrodes/runs/${run.id}/files/${name.split('/').map(encodeURIComponent).join('/')}`} download>{name}</Anchor>)}
      {files.isFetching && <Text size="xs" c="dimmed">Загрузка…</Text>}
      {files.data?.length === 0 && <Text size="xs" c="dimmed">Файлов нет</Text>}
      {files.error && <Text c="red" size="sm">{files.error.message}</Text>}
    </Stack>
  </details>;
}

function RunResult({ run, disabled, onContinue }) {
  return <Stack gap="xs">
    <Group justify="space-between" gap="xs">
      <Group gap="xs"><Badge size="sm" variant="light">{runStatus[run.status] ?? run.status}</Badge><Text size="xs" c="dimmed">{new Date(run.created_at).toLocaleString()}</Text></Group>
      <Button size="compact-xs" variant="subtle" disabled={disabled} onClick={() => onContinue(run.id)}>Исправить / продолжить</Button>
    </Group>
    {run.summary && <TaskMarkdown>{run.summary}</TaskMarkdown>}
    {run.error && <Text c="red" size="sm">{run.error}</Text>}
    <RunFiles run={run} />
  </Stack>;
}

function ContextForm({ taskId, context }) {
  const client = useQueryClient();
  const [answer, setAnswer] = useState(null);
  const analyze = useMutation({
    mutationFn: () => agentRequest(`/integrations/tasks/${taskId}/monocle/analyze`, { method: 'POST' }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['context', taskId] });
      await client.invalidateQueries({ queryKey: ['tasks'] });
      await client.invalidateQueries({ queryKey: ['ameba-ranking'] });
    },
    onSettled: () => client.invalidateQueries({ queryKey: ['context-files', taskId] }),
  });
  const save = useMutation({
    mutationFn: () => agentRequest(`/integrations/tasks/${taskId}/monocle`, { method: 'POST', body: JSON.stringify({ subject: context.data?.subject ?? '', answer: answer ?? context.data?.owner_notes ?? '' }) }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['context', taskId] });
      await client.invalidateQueries({ queryKey: ['tasks'] });
      await client.invalidateQueries({ queryKey: ['ameba-ranking'] });
    },
    onSettled: () => client.invalidateQueries({ queryKey: ['context-files', taskId] }),
  });
  return <Paper withBorder p="sm"><Stack gap="xs">
    <Title order={5}>Контекст · Monocle</Title>
    <Textarea label="Что ещё сделать Monocle по задаче" placeholder="Что проверить, где искать сведения, уточнения или ответы на вопросы ниже" autosize minRows={2} maxRows={6} maxLength={12000} value={answer ?? context.data?.owner_notes ?? ''} onChange={(event) => setAnswer(event.currentTarget.value)} />
    <Group gap="xs">
      <Button size="xs" loading={save.isPending} disabled={analyze.isPending || !(answer ?? context.data?.owner_notes ?? '').trim()} onClick={() => save.mutate()}>Передать Monocle</Button>
      <Button size="xs" variant="subtle" loading={analyze.isPending} disabled={save.isPending} onClick={() => analyze.mutate()}>Обновить контекст</Button>
    </Group>
    <details><summary style={{ cursor: 'pointer', fontSize: 13 }}>Файлы и управление браузером</summary><Stack gap="xs" mt="xs">
      <ContextFiles taskId={taskId} disabled={analyze.isPending || save.isPending} />
      <ContextBrowser taskId={taskId} disabled={analyze.isPending || save.isPending} />
    </Stack></details>
    {analyze.isSuccess && <Text size="sm">{analyze.data.status === 'ready' ? 'Контекста достаточно для оценки Ameba.' : 'Monocle запросил уточнения — ответь на вопросы ниже.'}</Text>}
    {context.data?.usage && <Text size="xs" c="dimmed">Последняя проверка: вход {context.data.usage.input_tokens ?? 'нет данных'}, выход {context.data.usage.output_tokens ?? 'нет данных'} токенов</Text>}
    {context.data?.questions?.length > 0 && <Alert color="yellow"><Stack gap="xs">{context.data.questions.map((question, i) => <Text key={i} size="sm">{question}</Text>)}</Stack></Alert>}
    {context.data?.usage?.updated_fields?.length > 0 && <Text size="sm" c="dimmed">Monocle обновил: {context.data.usage.updated_fields.map((name) => ({ title: 'название', description: 'описание', category: 'категорию' })[name]).join(', ')}.</Text>}
    {context.data?.usage?.sources?.length > 0 && <Stack gap="xs"><Text size="sm" fw={500}>Использованные страницы</Text>{context.data.usage.sources.map((source) => <Anchor key={source.url} href={source.url} target="_blank" rel="noreferrer" size="sm">{source.title}</Anchor>)}</Stack>}
    {save.data?.metrics?.status === 'needs_context' && <Text c="yellow">Нужны дополнительные уточнения — см. вопросы выше.</Text>}
    {save.data?.rank != null && <Text>Порядок Ameba: {save.data.rank} из {save.data.ranked_count}</Text>}
    <RankingAssessment assessment={save.data?.reasons?.find((item) => item.task_id === taskId)} />
    {(save.error || analyze.error || context.error) && <Alert color="red">{(save.error || analyze.error || context.error).message}<AgentProblemReport agent="Monocle" toolName="context_analysis" /></Alert>}
  </Stack></Paper>;
}

export default function TaskAutomation({ task, features = {} }) {
  const client = useQueryClient();
  const [notice, setNotice] = useState('');
  const [instruction, setInstruction] = useState('');
  const [resumeRunId, setResumeRunId] = useState(null);
  const queue = useQuery({ queryKey: ['agent-queue'], queryFn: () => request('/integrations/queue'),
    enabled: features.ameba !== false, refetchInterval: 1000 });
  const context = useQuery({ queryKey: ['context', task.id], queryFn: () => request(`/integrations/tasks/${task.id}/monocle`), refetchInterval: 5000, enabled: features.monocle !== false });
  const runs = useQuery({ queryKey: ['integrations', 'runs', task.id], queryFn: () => request(`/integrations/tasks/${task.id}/runs`), refetchInterval: 5000, retry: false, enabled: features.arrodes !== false });
  const action = useMutation({
    mutationFn: (provider) => (provider === 'ameba' ? agentRequest : request)(
      provider === 'ameba' && queue.data?.ameba_running ? '/integrations/ameba/rank' : `/integrations/tasks/${task.id}/${provider}`,
      { method: 'POST', ...(provider === 'arrodes' ? { body: JSON.stringify({ instruction, resume_run_id: resumeRunId }) } : {}) }),
    onSuccess: async (data, provider) => {
      setNotice(provider === 'arrodes' ? '' : data.unchanged ? data.status === 'running' ? 'Ameba уже работает с этими данными.' : 'Данные не изменились — порядок сохранён.' : data.metrics?.status === 'needs_context' ? `Monocle: недостаточно контекста. ${data.metrics.reason}` : data.rank != null ? `Порядок Ameba: ${data.rank} из ${data.ranked_count}` : 'Порядок Ameba обновлён');
      await client.invalidateQueries({ queryKey: ['ameba-ranking'] });
      await client.invalidateQueries({ queryKey: ['tasks'] });
      await client.invalidateQueries({ queryKey: ['context', task.id] });
      await client.invalidateQueries({ queryKey: ['context-files', task.id] });
      await client.invalidateQueries({ queryKey: ['integrations', 'runs', task.id] });
    },
  });
  const stop = useMutation({
    mutationFn: () => request('/integrations/arrodes/stop', { method: 'POST' }),
    onSettled: () => client.invalidateQueries({ queryKey: ['integrations', 'runs', task.id] }),
  });
  const arrodesRunning = runs.data?.some((run) => ['queued', 'running'].includes(run.status));
  const results = (runs.data ?? []).filter((run) => !['queued', 'running'].includes(run.status));
  const continueRun = (id) => { setResumeRunId(id); setInstruction(''); };
  return <Stack>
    <Group className="page-heading" justify="space-between"><Title order={2}>{task.title}</Title><Button component="a" href={`/calendar?add_task=${task.id}`} variant="light" size="xs">В календарь</Button></Group>
    {task.description && <Text style={{ whiteSpace: 'pre-wrap' }}>{task.description}</Text>}
    {task.category && <Badge variant="light">{task.category}</Badge>}
    {features.monocle !== false && (context.data?.full_markdown || context.data?.summary) && <Paper withBorder p="md"><Stack>
      <Title order={4}>Полная версия задачи</Title>
      {context.data.status === 'new' && <Text size="sm" c="yellow">Контекст изменился. Полную версию нужно обновить через Monocle.</Text>}
      <TaskMarkdown>{context.data.full_markdown || context.data.summary}</TaskMarkdown>
    </Stack></Paper>}
    {features.arrodes !== false && <Paper withBorder p="sm"><Stack gap="sm">
      <Group justify="space-between"><Title order={5}>Arrodes</Title>{resumeRunId && <Button variant="subtle" size="compact-xs" disabled={arrodesRunning} onClick={() => setResumeRunId(null)}>Новое поручение</Button>}</Group>
      <Textarea aria-label="Поручение Arrodes" placeholder={resumeRunId ? 'Что исправить или продолжить?' : 'Что сделать по этой задаче?'} value={instruction} onChange={(event) => setInstruction(event.currentTarget.value)} autosize minRows={2} maxRows={6} maxLength={12000} />
      <Group gap="xs">
        <Button size="xs" loading={action.isPending && action.variables === 'arrodes'} disabled={action.isPending || arrodesRunning} onClick={() => action.mutate('arrodes')}>{resumeRunId ? 'Продолжить' : 'Выполнить'}</Button>
        {arrodesRunning && <Button variant="subtle" color="red" size="xs" loading={stop.isPending} onClick={() => stop.mutate()}>Остановить</Button>}
      </Group>
      {stop.error && <Text c="red" size="sm">{stop.error.message}</Text>}
      {results[0] && <RunResult run={results[0]} disabled={arrodesRunning || action.isPending} onContinue={continueRun} />}
      {results.length > 1 && <details><summary style={{ cursor: 'pointer', fontSize: 13 }}>Предыдущие запуски · {results.length - 1}</summary><Stack gap="md" mt="sm">{results.slice(1).map((run) => <details key={run.id}><summary style={{ cursor: 'pointer', fontSize: 13 }}>{new Date(run.created_at).toLocaleString()} · {runStatus[run.status] ?? run.status}</summary><Stack mt="xs"><RunResult run={run} disabled={arrodesRunning || action.isPending} onContinue={continueRun} /></Stack></details>)}</Stack></details>}
    </Stack></Paper>}
    {features.ameba !== false && <Group><Button size="xs" variant="subtle" disabled={(action.isPending && !queue.data?.ameba_running) || queue.data?.browser_busy} onClick={() => action.mutate('ameba')}>{queue.data?.ameba_running ? 'Обновить при изменениях' : 'Обновить порядок Ameba'}</Button></Group>}
    <AgentProgress taskId={task.id} providers={['monocle', 'arrodes', 'ameba'].filter((name) => features[name] !== false)} active={action.isPending || arrodesRunning} />
    {features.monocle !== false && <ContextForm key={task.id} taskId={task.id} context={context} />}
    {notice && <Alert color="purple">{notice}</Alert>}
    {(action.error || runs.error) && <Alert color="red">{(action.error || runs.error).message}</Alert>}

  </Stack>;
}
