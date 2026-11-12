import { useEffect, useState } from 'react';
import { Alert, Badge, Button, Checkbox, Container, Group, Loader, Paper, SegmentedControl, Select, SimpleGrid, Stack, Table, Text, Textarea, Title, Tooltip } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import TaskCard from './TaskCard';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { uploadTaskFiles } from './fileUpload';
import { agentRequest, deleteTask, getTasks, request, saveTask } from './api';
import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc';
import TaskForm, { priorities, statuses } from './TaskForm';
import AmebaProgress from './AmebaProgress';
import AgentQueue from './AgentQueue';
import AgentProgress from './AgentProgress';
import RankingAssessment from './RankingAssessment';

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
  { value: 'ameba:asc', label: 'Порядок Ameba' },
];

export default function TaskBoard({ features = {} }) {
  const queryClient = useQueryClient();
  const compact = useMediaQuery('(max-width: 62em)', window.innerWidth <= 992);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [plannerOpen, setPlannerOpen] = useState(false);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const [view, setView] = useState('active');
  const [completing, setCompleting] = useState(null);
  const archived = view === 'archive';
  const [filters, setFilters] = useState({ status: null, priority: null, category: null });
  const [sort, setSort] = useState(features.ameba !== false ? 'ameba:asc' : 'deadline:asc');
  const [editor, setEditor] = useState(null);
  const [planFeedback, setPlanFeedback] = useState(null);
  const taskView = view === 'active' || view === 'archive';
  const automationEnabled = features.codex !== false || features.ameba !== false || features.context !== false;
  const currentSort = features.ameba === false && sort === 'ameba:asc' ? 'deadline:asc' : sort;
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
    queryClient.invalidateQueries({ queryKey: ['ameba-ranking'] }),
  ]);
  const ranking = useQuery({ queryKey: ['ameba-ranking'], queryFn: () => request('/integrations/ameba/ranking'),
    enabled: features.ameba !== false && !archived, refetchInterval: 15000 });
  const queue = useQuery({ queryKey: ['agent-queue'], queryFn: () => request('/integrations/queue'),
    enabled: !archived && (features.context !== false || features.ameba !== false), refetchInterval: 1000 });
  const rank = useMutation({ mutationFn: () => agentRequest('/integrations/ameba/rank', { method: 'POST', body: JSON.stringify({ feedback: planFeedback ?? ranking.data?.feedback ?? '' }) }),
    onSuccess: async () => { setSort('ameba:asc'); await queryClient.invalidateQueries({ queryKey: ['ameba-ranking'] }); await refresh(); } });
  const clearPlan = useMutation({
    mutationFn: () => request('/integrations/ameba/plan', { method: 'DELETE' }),
    onSuccess: async () => {
      rank.reset();
      await Promise.all([refresh(), queryClient.invalidateQueries({ queryKey: ['calendar'] })]);
    },
  });
  const rankingReasons = new Map((ranking.data?.reasons ?? []).map((item) => [item.task_id, item]));
  const positions = new Map((ranking.data?.task_ids ?? []).map((id, index) => [id, index + 1]));
  const save = useMutation({
    mutationFn: async (variables) => {
      const task = await saveTask(variables);
      try {
        await uploadTaskFiles(task.id, variables.files ?? []);
      } catch (error) {
        error.savedTask = task;
        error.message = `Задача сохранена, но не все файлы прикреплены: ${error.message}`;
        throw error;
      }
      return task;
    },
    onError: (error) => { if (error.savedTask) refresh(); },
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
  const rows = tasks.data ?? [];
  const error = tasks.error || update.error || remove.error || ranking.error || rank.error || clearPlan.error;
  const changeFilter = (name, value) => setFilters((current) => ({ ...current, [name]: value }));

  return (
    <Container size="xl" py="md" className="task-board">
      <Stack gap="md">
        <Group justify="space-between" className="page-heading task-heading">
          <Group gap="sm">
            <div><Text size="xs" c="dimmed" className="page-eyebrow">МОЙ ДЕНЬ</Text>
            <Title order={2}>Задачи <span className="task-count">{rows.length}</span></Title></div>
          </Group>
          <Button onClick={() => { save.reset(); setEditor({ task: null }); }}>{compact ? '+ Задача' : 'Добавить задачу'}</Button>
        </Group>
        <SegmentedControl
          value={view}
          onChange={(value) => { setView(value); setFilters({ status: null, priority: null, category: null }); }}
          data={[{ value: 'active', label: 'Задачи' }, { value: 'archive', label: 'Архив' }]}
          styles={{ root: { flexWrap: 'wrap' } }}
        />
        {taskView && <>
        <Paper withBorder p="sm" radius="md" className="task-filter-panel">
          {compact && <Button fullWidth variant="subtle" color="gray" onClick={() => setFiltersOpen(!filtersOpen)} aria-expanded={filtersOpen}>Фильтры и сортировка{Object.values(filters).filter(Boolean).length > 0 ? ` · ${Object.values(filters).filter(Boolean).length}` : ''} {filtersOpen ? '−' : '+'}</Button>}
          {(!compact || filtersOpen) && <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }}>
            <Select label="Статус" placeholder="Все статусы" data={statuses} clearable value={filters.status} onChange={(value) => changeFilter('status', value)} />
            <Select label="Приоритет" placeholder="Все приоритеты" data={[{ value: 'overdue', label: 'Просрочено' }, ...priorities]} clearable value={filters.priority} onChange={(value) => changeFilter('priority', value)} />
            <Select label="Категория" placeholder="Все категории" data={categories.data ?? []} clearable searchable value={filters.category} onChange={(value) => changeFilter('category', value)} />
            <Select label="Сортировка" data={sorting.filter((item) => features.ameba !== false || item.value !== 'ameba:asc')} value={currentSort} allowDeselect={false} onChange={setSort} />
          </SimpleGrid>}
        </Paper>
        {!archived && features.context !== false && features.ameba === false && <AgentProgress providers={['context']} active={queue.data?.active?.some((item) => item.provider === 'context')} />}
        {!archived && (features.context !== false || features.ameba !== false) && <AgentQueue queue={queue.data} error={queue.error} />}
        {features.ameba !== false && !archived && <Paper withBorder radius="lg" p="md" className="planner-panel"><Stack gap="sm">
          <Group justify="space-between"><Group gap="xs"><span className={`planner-dot ${queue.data?.ameba_running ? 'is-active' : ''}`} /><Text fw={600} size="sm">План дня</Text></Group><Button size="compact-sm" variant="subtle" onClick={() => setPlannerOpen(!plannerOpen)} aria-expanded={plannerOpen}>{plannerOpen ? 'Свернуть' : 'Настроить'}</Button></Group>
          {plannerOpen && <>
          <Textarea label="Пожелания к расписанию" placeholder="Например: оставь утро свободным, а лабораторную перенеси на вечер"
            autosize minRows={1} maxRows={4} maxLength={4000} value={planFeedback ?? ranking.data?.feedback ?? ''}
            onChange={(event) => setPlanFeedback(event.currentTarget.value)} />
          </>}<Group>
          <Button variant="light" loading={rank.isPending && !queue.data?.ameba_running} disabled={clearPlan.isPending || queue.data?.browser_busy} onClick={() => rank.mutate()}>{queue.data?.ameba_running ? 'Применить изменения' : 'Обновить расписание'}</Button>
          <Button variant="subtle" color="red" loading={clearPlan.isPending} disabled={!ranking.data?.has_plan || rank.isPending || queue.data?.ameba_running} onClick={() => clearPlan.mutate()}>Удалить план</Button>
          <Text size="sm" c="dimmed">{ranking.data?.needs_refresh ? 'Порядок нужно обновить.' : `Сравнено задач: ${ranking.data?.ranked_count ?? 0}.`}{ranking.data?.pending_count > 0 && ` Ожидают контекста Monocle: ${ranking.data.pending_count}.`}{ranking.data?.uncertain_comparisons > 0 && ' При неуверенном сравнении сохранён ручной приоритет и дедлайн.'}</Text>
        </Group>{rank.data?.unchanged && <Text size="xs" c="dimmed">{rank.data.status === 'running' ? 'Ameba уже работает с этими данными.' : 'Данные не изменились — сохранённый порядок остаётся.'}</Text>}{ranking.data?.explanation && <Tooltip multiline w={320} label={ranking.data.explanation} events={{ hover: true, focus: true, touch: true }}><Text component="button" type="button" size="xs" c="dimmed" style={{ cursor: 'help', background: 'none', border: 0, padding: 0, width: 'fit-content' }}>{ranking.data.needs_refresh ? 'О последней сортировке' : 'Почему такой порядок'}</Text></Tooltip>}<div className="planner-progress"><AmebaProgress active={rank.isPending} /></div></Stack></Paper>}
        {error && <Alert color="red">{error.message}</Alert>}
        {tasks.isPending ? <div className="page-loading"><Loader aria-label="Загрузка задач" /></div> : (
          compact ? <div className="task-card-grid">{rows.map((task) => <TaskCard key={task.id} task={task} archived={archived}
            completing={completing === task.id} busy={update.isPending} removing={remove.isPending}
            position={features.ameba !== false ? positions.get(task.id) : null} assessment={features.ameba !== false ? rankingReasons.get(task.id) : null}
            overdue={isOverdue(task, now)} agents={automationEnabled}
            onUpdate={(data) => update.mutate({ id: task.id, data })}
            onEdit={() => { save.reset(); setEditor({ task }); }}
            onRemove={() => { if (!archived || window.confirm(`Удалить задачу «${task.title}» навсегда?`)) remove.mutate({ id: task.id, permanent: archived }); }}
          />)}{!rows.length && <Paper p="xl" withBorder radius="lg"><Text ta="center" c="dimmed">{archived ? 'Архив пуст' : 'Задач нет'}</Text></Paper>}</div> : <Paper withBorder radius="lg" p="sm">
            <Table.ScrollContainer minWidth={0} className="task-table">
              <Table verticalSpacing="xs" highlightOnHover>
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
                      <Table.Td data-label="Дедлайн" style={{ whiteSpace: 'nowrap' }}><Text size="sm" c={isOverdue(task, now) ? 'red' : undefined}>{task.deadline ? dayjs.utc(task.deadline).local().format('DD.MM.YYYY HH:mm') : '—'}</Text>{isOverdue(task, now) && <Text size="xs" c="red">Просрочено</Text>}</Table.Td>
                      <Table.Td data-label="Приоритет"><Badge color={{ low: 'gray', medium: 'yellow', high: 'red' }[task.priority]} variant="light">{priorities.find((item) => item.value === task.priority)?.label}</Badge>{features.context !== false && task.ai_details && JSON.parse(task.ai_details).status === 'needs_context' && <Text size="xs" c="yellow">Недостаточно контекста. {JSON.parse(task.ai_details).reason} <a href={`/?task=${task.id}`}>Ответить</a></Text>}{features.context !== false && task.ai_details && JSON.parse(task.ai_details).status === 'context_pending' && <Text size="xs" c="dimmed">Ожидает проверки Monocle</Text>}{features.ameba !== false && positions.has(task.id) && <Text size="xs" c="dimmed">Порядок Ameba: {positions.get(task.id)} из {ranking.data.ranked_count}</Text>}{features.ameba !== false && positions.has(task.id) && rankingReasons.has(task.id) && <RankingAssessment assessment={rankingReasons.get(task.id)} />}</Table.Td>
                      <Table.Td data-label="Статус"><Select aria-label={`Статус: ${task.title}`} w={{ base: '100%', sm: 160 }} data={statuses} value={completing === task.id ? 'done' : task.status} allowDeselect={false} disabled={update.isPending || archived} onChange={(value) => update.mutate({ id: task.id, data: { status: value } })} /></Table.Td>
                      <Table.Td data-label="Категория">{task.category || '—'}</Table.Td>
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
                          {automationEnabled && <Button size="xs" variant="light" component="a" href={`/?task=${task.id}`}>Открыть агента</Button>}
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
      {editor && <TaskForm task={editor.task} onClose={() => setEditor(null)} allowAutomation={automationEnabled} allowContext={features.context !== false} onSubmit={(data, options) => save.mutate({ id: editor.task?.id ?? options.taskId, data, ...options })} pending={save.isPending} error={save.error} />}
    </Container>
  );
}
