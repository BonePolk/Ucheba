import { useLayoutEffect, useRef, useState } from 'react';
import { Badge, Button, Group, Paper, Stack, Text, Tooltip } from '@mantine/core';

function duration(minutes) {
  return minutes < 60 ? `${minutes} мин` : `${Math.round(minutes / 60 * 10) / 10} ч`;
}

const rest = { break: 'Перерыв', meal: 'Еда', personal: 'Личное время', sleep: 'Сон' };

export default function AgentTimeline({ tasks = [], finished = false, schedule }) {
  const [expanded, setExpanded] = useState(false);
  const chart = useRef(null);
  const seen = useRef(new Set());
  useLayoutEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    for (const element of chart.current?.querySelectorAll('[data-block-key]') ?? []) {
      const key = element.dataset.blockKey;
      if (!seen.current.has(key)) {
        element.animate([{ transform: 'scaleX(0.02)' }, { transform: 'scaleX(1)' }],
          { duration: 450, easing: 'cubic-bezier(.22,1,.36,1)' });
        seen.current.add(key);
      }
    }
  });
  const planned = tasks.filter((task) => task.plan);
  const byId = new Map(planned.map((task) => [task.id, task]));
  let elapsed = 0;
  const blocks = schedule?.blocks ?? planned.map((task) => {
    const block = { id: `task-${task.id}-0`, kind: 'task', task_id: task.id, start: elapsed, minutes: task.plan.max_minutes };
    elapsed += block.minutes;
    return block;
  });
  const total = blocks.reduce((end, block) => Math.max(end, block.start + block.minutes), 0);
  const days = Math.max(1, Math.ceil(total / 1440));
  const visibleDays = expanded ? days : Math.min(days, 3);
  const slices = [];
  for (const block of blocks) {
    let start = block.start;
    let remaining = block.minutes;
    let part = 0;
    while (remaining > 0 && start < visibleDays * 1440) {
      const day = Math.floor(start / 1440);
      const minutes = Math.min(remaining, 1440 - start % 1440);
      slices.push({ ...block, key: `${block.id}-${part}`, day, left: start % 1440 / 14.4, width: minutes / 14.4, minutes });
      start += minutes;
      remaining -= minutes;
      part += 1;
    }
  }
  return <Paper withBorder radius="md" p="sm"><Stack gap={8}>
    <Group justify="space-between">
      <Tooltip multiline w={300} label={`${schedule?.rest_assumption ?? 'Время оценивается приблизительно'}${schedule?.start_at ? ` Начало: ${new Date(schedule.start_at).toLocaleString(undefined, { timeZone: schedule.timezone })}` : ''}`}><Text size="xs" c="dimmed">{finished ? 'Итоговый план' : 'План работы'}</Text></Tooltip>
      <Group gap={6}>{!finished && <Badge size="xs" variant="light" color="gray">Черновик</Badge>}<Text size="xs" fw={600}>{duration(schedule?.max_minutes ?? planned.reduce((sum, task) => sum + task.plan.max_minutes, 0))} работы</Text></Group>
    </Group>
    {schedule?.timeline_error ? <Text size="xs" c="yellow">Шкала не построена: {schedule.timeline_error}. Порядок задач сохранён.</Text> : planned.length > 0 ? <div style={{ overflowX: 'auto', padding: '5px 0' }}>
      <div ref={chart} style={{ position: 'relative', minWidth: 760, height: visibleDays * 66 + 18 }}>
        {Array.from({ length: visibleDays }, (_, day) => <div key={day} style={{ position: 'absolute', top: day * 66, width: '100%' }}>
          <Text size="xs" c="dimmed">День {day + 1}</Text>
          <div style={{ position: 'absolute', top: 24, width: '100%', height: 34, borderRadius: 6, background: 'var(--mantine-color-dark-7)' }} />
        </div>)}
        {slices.map((block) => {
          const task = byId.get(block.task_id);
          const label = task?.title ?? (block.title || rest[block.kind] || 'Занято');
          return <Tooltip key={block.key} multiline w={270} label={`${label} · ${duration(block.minutes)}${task?.plan.expected_result ? ` · ${task.plan.expected_result}` : ''}`}>
            <a data-block-key={block.key} className={`ameba-timeline-task ${block.kind !== 'task' ? 'ameba-timeline-rest' : ''}`} href={task ? `/?task=${task.id}` : undefined} aria-label={label} style={{ transformOrigin: 'left center', top: block.day * 66 + 24, left: `${block.left}%`, width: `calc(${block.width}% - 2px)`, background: task ? ['#7f49b4', '#64408c', '#9567bf'][task.id % 3] : undefined }}>{label}</a>
          </Tooltip>;
        })}
        <Group justify="space-between" style={{ position: 'absolute', bottom: 0, width: '100%' }}><Text size="xs" c="dimmed">Начало условного дня</Text><Text size="xs" c="dimmed">24 ч</Text></Group>
      </div>
    </div> : <Text size="xs" c="dimmed">Оцениваем задачи…</Text>}
    {days > 3 && <Button size="compact-xs" variant="subtle" onClick={() => setExpanded(!expanded)}>{expanded ? 'Свернуть' : `Все ${days} дней`}</Button>}
    <Group gap={6}><Badge size="xs" variant="light">Работа</Badge><Badge size="xs" color="gray" variant="light">Отдых и еда</Badge><Text size="xs" c="dimmed">{schedule?.start_at ? `По расписанию · ${schedule.timezone}` : 'Условные дни, не часы личного расписания'}</Text></Group>
    {schedule && <Group gap={6}>{Object.entries(schedule.potential_rewards ?? {}).map(([unit, value]) => <Badge key={unit} size="xs" variant="light">≈ {value} {unit}</Badge>)}</Group>}
    {tasks.length > planned.length && <Text size="xs" c="dimmed">Ещё {tasks.length - planned.length} в оценке</Text>}
  </Stack></Paper>;
}
