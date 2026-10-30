import { useState } from 'react';
import { Alert, Badge, Button, Checkbox, Container, Group, Loader, Paper, Select, SimpleGrid, Stack, Table, Text, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { deleteTask, getTasks, saveTask } from './api';
import TaskForm, { priorities, statuses } from './TaskForm';

const sorting = [
  { value: 'deadline:asc', label: 'Дедлайн: сначала ближайшие' },
  { value: 'deadline:desc', label: 'Дедлайн: сначала поздние' },
  { value: 'priority:asc', label: 'Приоритет: сначала высокий' },
  { value: 'priority:desc', label: 'Приоритет: сначала низкий' },
  { value: 'created_at:desc', label: 'Создание: сначала новые' },
  { value: 'created_at:asc', label: 'Создание: сначала старые' },
];

export default function App() {
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState({ status: null, priority: null, category: null });
  const [sort, setSort] = useState('deadline:asc');
  const [editor, setEditor] = useState(null);
  const [sortBy, order] = sort.split(':');
  const tasks = useQuery({
    queryKey: ['tasks', filters, sort],
    queryFn: () => getTasks({ ...filters, sort_by: sortBy, order }),
  });
  const allTasks = useQuery({ queryKey: ['tasks', 'all'], queryFn: () => getTasks() });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['tasks'] });
  const save = useMutation({
    mutationFn: saveTask,
    onSuccess: () => { setEditor(null); return refresh(); },
  });
  const update = useMutation({ mutationFn: saveTask, onSuccess: refresh });
  const remove = useMutation({ mutationFn: deleteTask, onSuccess: refresh });
  const categories = [...new Set((allTasks.data ?? []).map((task) => task.category).filter(Boolean))].sort();
  const error = tasks.error || update.error || remove.error;
  const changeFilter = (name, value) => setFilters((current) => ({ ...current, [name]: value }));

  return (
    <Container size="xl" py="xl">
      <Stack gap="lg">
        <Group justify="space-between">
          <Title order={1}>Задачи</Title>
          <Button onClick={() => { save.reset(); setEditor({ task: null }); }}>Добавить задачу</Button>
        </Group>
        <Paper withBorder p="md" radius="md">
          <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }}>
            <Select label="Статус" placeholder="Все статусы" data={statuses} clearable value={filters.status} onChange={(value) => changeFilter('status', value)} />
            <Select label="Приоритет" placeholder="Все приоритеты" data={priorities} clearable value={filters.priority} onChange={(value) => changeFilter('priority', value)} />
            <Select label="Категория" placeholder="Все категории" data={categories} clearable searchable value={filters.category} onChange={(value) => changeFilter('category', value)} />
            <Select label="Сортировка" data={sorting} value={sort} allowDeselect={false} onChange={setSort} />
          </SimpleGrid>
        </Paper>
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
                  {(tasks.data ?? []).map((task) => (
                    <Table.Tr key={task.id}>
                      <Table.Td>
                        <Checkbox aria-label={`Выполнено: ${task.title}`} checked={task.status === 'done'} disabled={update.isPending} onChange={(event) => update.mutate({ id: task.id, data: { status: event.currentTarget.checked ? 'done' : 'todo' } })} />
                      </Table.Td>
                      <Table.Td miw={180} maw={320}>
                        <Text fw={500} td={task.status === 'done' ? 'line-through' : undefined} style={{ overflowWrap: 'anywhere' }}>{task.title}</Text>
                        {task.description && <Text size="sm" c="dimmed" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{task.description}</Text>}
                      </Table.Td>
                      <Table.Td style={{ whiteSpace: 'nowrap' }}>{task.deadline ? task.deadline.split('-').reverse().join('.') : '—'}</Table.Td>
                      <Table.Td><Badge color={{ low: 'gray', medium: 'yellow', high: 'red' }[task.priority]} variant="light">{priorities.find((item) => item.value === task.priority)?.label}</Badge></Table.Td>
                      <Table.Td><Select aria-label={`Статус: ${task.title}`} w={160} data={statuses} value={task.status} allowDeselect={false} disabled={update.isPending} onChange={(value) => update.mutate({ id: task.id, data: { status: value } })} /></Table.Td>
                      <Table.Td>{task.category || '—'}</Table.Td>
                      <Table.Td>
                        <Group gap="xs" wrap="nowrap">
                          <Button size="xs" variant="default" onClick={() => { save.reset(); setEditor({ task }); }}>Изменить</Button>
                          <Button size="xs" variant="subtle" color="red" disabled={remove.isPending} onClick={() => { if (window.confirm(`Удалить задачу «${task.title}»?`)) remove.mutate(task.id); }}>Удалить</Button>
                        </Group>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
            {tasks.data?.length === 0 && <Text ta="center" c="dimmed" py="xl">Задач нет</Text>}
          </Paper>
        )}
      </Stack>
      {editor && <TaskForm task={editor.task} onClose={() => setEditor(null)} onSubmit={(data) => save.mutate({ id: editor.task?.id, data })} pending={save.isPending} error={save.error} />}
    </Container>
  );
}
