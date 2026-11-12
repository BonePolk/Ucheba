import { Badge, Button, Checkbox, Group, Menu, Paper, Select, Stack, Text } from '@mantine/core';
import dayjs from 'dayjs';
import { priorities, statuses } from './TaskForm';
import RankingAssessment from './RankingAssessment';

export default function TaskCard({ task, archived, completing, busy, removing, position, assessment, onUpdate, onEdit, onRemove, overdue, agents }) {
  let context = {};
  try { context = JSON.parse(task.ai_details || '{}'); } catch { /* Старые данные не мешают открыть задачу. */ }
  const done = task.status === 'done' || completing;
  return <Paper component="article" withBorder radius="lg" p="md" className={`task-card ${completing ? 'is-completing' : ''}`}>
    <Group justify="space-between" wrap="nowrap" align="start">
      <Group gap="xs" className="task-card-tags">
        {position && <span className="task-position">{String(position).padStart(2, '0')}</span>}
        <Badge size="xs" variant="light" color={{ high: 'red', medium: 'yellow', low: 'gray' }[task.priority]}>{priorities.find((item) => item.value === task.priority)?.label}</Badge>
        {task.category && <Text size="xs" c="dimmed" lineClamp={1}>{task.category}</Text>}
      </Group>
      <Menu position="bottom-end" withinPortal>
        <Menu.Target><Button size="compact-sm" variant="subtle" color="gray" aria-label={`Действия: ${task.title}`}>•••</Button></Menu.Target>
        <Menu.Dropdown>
          {!archived && <Menu.Item onClick={onEdit}>Изменить задачу</Menu.Item>}
          {archived && <Menu.Item disabled={busy} onClick={() => onUpdate({ archived: false })}>Восстановить</Menu.Item>}
          <Menu.Item color="red" disabled={removing} onClick={onRemove}>{archived ? 'Удалить навсегда' : 'В архив'}</Menu.Item>
        </Menu.Dropdown>
      </Menu>
    </Group>
    <Group gap="sm" wrap="nowrap" align="start" mt="xs">
      <Checkbox mt={4} aria-label={`Выполнено: ${task.title}`} checked={done} disabled={busy || archived} onChange={(event) => onUpdate({ status: event.currentTarget.checked ? 'done' : 'todo' })} />
      <Stack gap={5} style={{ minWidth: 0, flex: 1 }}>
        <Text component="a" href={`/?task=${task.id}`} fw={600} c="inherit" className="task-card-title" td={done ? 'line-through' : undefined}>{task.title}</Text>
        {task.description && <Text size="sm" c="dimmed" lineClamp={2}>{task.description}</Text>}
      </Stack>
    </Group>
    {context.status === 'needs_context' && <Text size="xs" c="yellow" mt="sm" component="a" href={`/?task=${task.id}`}>Monocle ждёт ответа →</Text>}
    <div className="task-card-footer">
      <div><Text size="xs" c={overdue ? 'red' : 'dimmed'}>{task.deadline ? dayjs.utc(task.deadline).local().format('DD MMM · HH:mm') : 'Без срока'}</Text></div>
      {!archived && <Select size="xs" aria-label={`Статус: ${task.title}`} data={statuses} value={done ? 'done' : task.status} allowDeselect={false} disabled={busy} onChange={(status) => onUpdate({ status })} />}
      {archived && <Badge size="xs" color="gray">Архив</Badge>}
    </div>
    {assessment && <details className="task-assessment"><summary>Оценка Амёбы</summary><RankingAssessment assessment={assessment} compact /></details>}
    {agents && !archived && <Button component="a" href={`/?task=${task.id}`} variant="subtle" size="xs" fullWidth mt="xs">Открыть задачу и агента →</Button>}
  </Paper>;
}
