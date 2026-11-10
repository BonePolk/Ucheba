import { useEffect, useState } from 'react';
import { Alert, Badge, Button, Checkbox, Container, Group, Loader, Paper, SegmentedControl, Select, SimpleGrid, Stack, Table, Text, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { agentRequest, deleteTask, getTasks, request, saveTask } from './api';
import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc';
import TaskForm, { priorities, statuses } from './TaskForm';
import JebaProgress from './JebaProgress';

dayjs.extend(utc);

function isOverdue(task, now) {
  return !task.archived && task.status !== 'done' && task.deadline && dayjs.utc(task.deadline).valueOf() < now;
}

const sorting = [
  { value: 'deadline:asc', label: 'Дедлайн: сначала ближайшие' },
  { value: 'deadline:desc', label: 'Дедлайн: сначала поздние' },
  { value: 'priority:asc', label: 'Приоритет: сначала высокий' },
  { value: 'priority:desc', label: 'Приоритет: сначала низкий' },
  { value: 'created_at:desc', label: 'Создание: сначала новые' },
  { value: 'created_at:asc', label: 'Создание: сначала старые' },
  { value: 'jev:asc', label: 'Порядок Jeba' },
];

export default function TaskBoard({ features = {} }) {
  const queryClient = useQueryClient();
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const [view, setView] = useState('active');
  const [completing, setCompleting] = useState(null);
  const archived = view === 'archive';
  const [filters, setFilters] = useState({ status: null, priority: null, category: null });
  const [sort, setSort] = useState(features.jev !== false ? 'jev:asc' : 'deadline:asc');
  const [editor, setEditor] = useState(null);
  const taskView = view === 'active' || view === 'archive';
  const automationEnabled = features.codex !== false || features.jev !== false || features.context !== false;
  const currentSort = features.jev === false && sort === 'jev:asc' ? 'deadline:asc' : sort;
  const [sortBy, order] = currentSort.split(':');
  const tasks = useQuery({
    queryKey: ['tasks', filters, currentSort, archived],
    queryFn: () => getTasks({ ...filters, sort_by: sortBy, order, archived: String(archived) }),
    refetchInterval: 15000,
    enabled: taskView,
  });
  const categories = useQuery({ queryKey: ['tasks', 'categories', archived], queryFn: () => request(`/tasks/categories?archived=${archived}`), enabled: taskView });
  const refresh = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['tasks'] }),
    queryClient.invalidateQueries({ queryKey: ['jev-ranking'] }),
  ]);
  const ranking = useQuery({ queryKey: ['jev-ranking'], queryFn: () => request('/integrations/jev/ranking'),
    enabled: features.jev !== false && !archived, refetchInterval: 15000 });
  const rank = useMutation({ mutationFn: () => agentRequest('/integrations/jev/rank', { method: 'POST' }),
    onSuccess: async () => { setSort('jev:asc'); await queryClient.invalidateQueries({ queryKey: ['jev-ranking'] }); await refresh(); } });
  const positions = new Map((ranking.data?.task_ids ?? []).map((id, index) => [id, index + 1]));
  const save = useMutation({
    mutationFn: saveTask,
    onSuccess: (task, variables) => {
      setEditor(null);
      if (variables.openAutomation) window.location.assign(`/?task=${task.id}`);
      return refresh();
    },
  });
  const update = useMutation({
    mutationFn: saveTask,
    onSuccess: async (task, { data }) => {
      if (data.status === 'done') {
        setCompleting(task.id);
        await new Promise((resolve) => setTimeout(resolve, 700));
      }
      await refresh();
      setCompleting(null);
    },
  });
  const remove = useMutation({ mutationFn: deleteTask, onSuccess: refresh });
  const rows = [...(tasks.data ?? [])].sort((a, b) => Number(Boolean(isOverdue(b, now))) - Number(Boolean(isOverdue(a, now))));
  const error = tasks.error || update.error || remove.error || ranking.error || rank.error;
  const changeFilter = (name, value) => setFilters((current) => ({ ...current, [name]: value }));

  return (
    <Container size="xl" py="xl">
      <Stack gap="lg">
        <Group justify="space-between">
          <Group gap="sm">
            <img src="/icon.png" alt="" width="44" height="44" style={{ borderRadius: 10 }} />
            <Title order={1}>Задачи</Title>
          </Group>
          <Button onClick={() => { save.reset(); setEditor({ task: null }); }}>Добавить задачу</Button>
        </Group>
        <SegmentedControl
          value={view}
          onChange={(value) => { setView(value); setFilters({ status: null, priority: null, category: null }); }}
          data={[{ value: 'active', label: 'Задачи' }, { value: 'archive', label: 'Архив' }]}
          styles={{ root: { flexWrap: 'wrap' } }}
        />
        {taskView && <>
        <Paper withBorder p="md" radius="md">
          <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }}>
            <Select label="Статус" placeholder="Все статусы" data={statuses} clearable value={filters.status} onChange={(value) => changeFilter('status', value)} />
            <Select label="Приоритет" placeholder="Все приоритеты" data={[{ value: 'overdue', label: 'Просрочено — наивысший' }, ...priorities]} clearable value={filters.priority} onChange={(value) => changeFilter('priority', value)} />
            <Select label="Категория" placeholder="Все категории" data={categories.data ?? []} clearable searchable value={filters.category} onChange={(value) => changeFilter('category', value)} />
            <Select label="Сортировка" data={sorting.filter((item) => features.jev !== false || item.value !== 'jev:asc')} value={currentSort} allowDeselect={false} onChange={setSort} />
          </SimpleGrid>
        </Paper>
        {features.jev !== false && !archived && <Stack gap="xs"><Group>
          <Button variant="light" loading={rank.isPending} onClick={() => rank.mutate()}>Обновить порядок Jeba</Button>
          <Text size="sm" c="dimmed">{ranking.data?.needs_refresh ? 'Порядок нужно обновить.' : `Сравнено задач: ${ranking.data?.ranked_count ?? 0}.`}{ranking.data?.pending_count > 0 && ` Ожидают контекста Lucheba: ${ranking.data.pending_count}.`}{ranking.data?.uncertain_comparisons > 0 && ' При неуверенном сравнении сохранён ручной приоритет и дедлайн.'}</Text>
        </Group><JebaProgress active={rank.isPending} /></Stack>}
        {error && <Alert color="red">{error.message}</Alert>}
        {tasks.isPending ? <Loader aria-label="Загрузка задач" /> : (
          <Paper withBorder radius="md" p="sm">
            <Table.ScrollContainer minWidth={900}>
              <Table verticalSpacing="sm" highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Готово</Table.Th><Table.Th>Задача</Table.Th><Table.Th>Дедлайн</Table.Th><Table.Th>Приоритет</Table.Th><Table.Th>Статус</Table.Th><Table.Th>Категория</Table.Th><Table.Th>Действия</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {rows.map((task) => (
                    <Table.Tr key={task.id} className={completing === task.id ? 'task-completing' : undefined}>
                      <Table.Td>
                        <Checkbox aria-label={`Выполнено: ${task.title}`} checked={task.status === 'done' || completing === task.id} disabled={update.isPending || archived} onChange={(event) => update.mutate({ id: task.id, data: { status: event.currentTarget.checked ? 'done' : 'todo' } })} />
                      </Table.Td>
                      <Table.Td miw={180} maw={320}>
                        <Text component="a" href={`/?task=${task.id}`} fw={500} c="inherit" td={task.status === 'done' || completing === task.id ? 'line-through' : undefined} style={{ overflowWrap: 'anywhere' }}>{task.title}</Text>
                        {task.source_url && <Text component="a" href={task.source_url} target="_blank" rel="noreferrer" size="xs" c="purple">Источник Telegram</Text>}
                        {task.description && <Text size="sm" c="dimmed" lineClamp={2} style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{task.description}</Text>}
                      </Table.Td>
                      <Table.Td style={{ whiteSpace: 'nowrap' }}><Text size="sm" c={isOverdue(task, now) ? 'red' : undefined}>{task.deadline ? dayjs.utc(task.deadline).local().format('DD.MM.YYYY HH:mm') : '—'}</Text>{isOverdue(task, now) && <Text size="xs" c="red">Просрочено</Text>}</Table.Td>
                      <Table.Td><Badge color={isOverdue(task, now) ? 'red' : { low: 'gray', medium: 'yellow', high: 'red' }[task.priority]} variant="light">{isOverdue(task, now) ? 'Наивысший' : priorities.find((item) => item.value === task.priority)?.label}</Badge>{features.context !== false && task.ai_details && JSON.parse(task.ai_details).status === 'needs_context' && <Text size="xs" c="yellow">Недостаточно контекста. {JSON.parse(task.ai_details).reason} <a href={`/?task=${task.id}`}>Ответить</a></Text>}{features.context !== false && task.ai_details && JSON.parse(task.ai_details).status === 'context_pending' && <Text size="xs" c="dimmed">Ожидает проверки Lucheba</Text>}{features.jev !== false && positions.has(task.id) && <Text size="xs" c="dimmed">Порядок Jeba: {positions.get(task.id)} из {ranking.data.ranked_count}</Text>}</Table.Td>
                      <Table.Td><Select aria-label={`Статус: ${task.title}`} w={160} data={statuses} value={completing === task.id ? 'done' : task.status} allowDeselect={false} disabled={update.isPending || archived} onChange={(value) => update.mutate({ id: task.id, data: { status: value } })} /></Table.Td>
                      <Table.Td>{task.category || '—'}</Table.Td>
                      <Table.Td>
                        <Group gap="xs">
                          {archived ? <>
                            <Button size="xs" variant="default" disabled={update.isPending} onClick={() => update.mutate({ id: task.id, data: { archived: false } })}>Восстановить</Button>
                            <Button size="xs" variant="subtle" color="red" disabled={remove.isPending} onClick={() => {
                              if (window.confirm(`Удалить задачу «${task.title}» навсегда? Восстановить её будет невозможно.`)) {
                                remove.mutate({ id: task.id, permanent: true });
                              }
                            }}>Удалить навсегда</Button>
                          </> : <>
                          {automationEnabled && <Button size="xs" variant="light" component="a" href={`/?task=${task.id}`}>Действия ИИ</Button>}
                          <Button size="xs" variant="default" onClick={() => { save.reset(); setEditor({ task }); }}>Изменить</Button>
                          <Button size="xs" variant="subtle" color="red" disabled={remove.isPending} onClick={() => remove.mutate({ id: task.id })}>В архив</Button>
                          </>}
                        </Group>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
            {tasks.data?.length === 0 && <Text ta="center" c="dimmed" py="xl">{archived ? 'Архив пуст' : 'Задач нет'}</Text>}
          </Paper>
        )}
        </>}
      </Stack>
      {editor && <TaskForm task={editor.task} onClose={() => setEditor(null)} allowAutomation={automationEnabled} onSubmit={(data, options) => save.mutate({ id: editor.task?.id, data, ...options })} pending={save.isPending} error={save.error} />}
    </Container>
  );
}
