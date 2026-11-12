import { calendarColors, calendarBlockColor } from './calendarColors';
import { useLayoutEffect, useRef, useState } from 'react';
import { useMediaQuery } from '@mantine/hooks';
import { Badge, Button, Group, Paper, Stack, Text, Tooltip } from '@mantine/core';

function duration(minutes) {
  return minutes < 60 ? `${minutes} мин` : `${Math.round(minutes / 60 * 10) / 10} ч`;
}

const rest = { break: 'Перерыв', meal: 'Еда', personal: 'Личное время', sleep: 'Сон' };

export default function AgentTimeline({ tasks = [], finished = false, schedule, calendarOnly = false }) {
  const compact = useMediaQuery('(max-width: 767px)', window.innerWidth < 768);
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
  const origin = schedule?.start_at ? new Date(schedule.start_at) : null;
  const zone = schedule?.timezone || 'UTC';
  const formatter = new Intl.DateTimeFormat('en-GB', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  function localTime(date) {
    const parts = Object.fromEntries(formatter.formatToParts(date).map((part) => [part.type, part.value]));
    return { date: Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day)), minutes: Number(parts.hour) * 60 + Number(parts.minute) };
  }
  const firstDate = origin ? localTime(origin).date : null;
  function clockPosition(minutes) {
    if (!origin) return minutes;
    const local = localTime(new Date(origin.getTime() + minutes * 60000));
    return (local.date - firstDate) / 86400000 * 1440 + local.minutes;
  }
  function dayLabel(day) {
    return firstDate === null ? `День ${day + 1}` : new Date(firstDate + day * 86400000).toLocaleDateString('ru-RU', { timeZone: 'UTC', weekday: 'long', day: 'numeric', month: 'long' });
  }
  const total = blocks.reduce((end, block) => Math.max(end, clockPosition(block.start + block.minutes)), 0);
  const days = Math.max(1, Math.ceil(total / 1440));
  const visibleDays = expanded ? days : Math.min(days, 3);
  const slices = [];
  for (const block of blocks) {
    let start = clockPosition(block.start);
    const end = clockPosition(block.start + block.minutes);
    // При повторном часе часы могут идти назад: длительность блока всё равно реальная.
    let remaining = end > start ? end - start : block.minutes;
    let part = 0;
    while (remaining > 0 && start < visibleDays * 1440) {
      const day = Math.floor(start / 1440);
      const minutes = Math.min(remaining, 1440 - start % 1440);
      slices.push({ ...block, total_minutes: block.minutes, key: `${block.id}-${part}`, day, left: start % 1440 / 14.4, width: minutes / 14.4, minutes });
      start += minutes;
      remaining -= minutes;
      part += 1;
    }
  }
  const agenda = [...blocks].filter((block) => block.kind !== 'work').sort((a, b) => a.start - b.start);
  return <Paper withBorder radius="md" p="sm"><Stack gap={8}>
    {(schedule?.deadline_conflicts ?? []).map((item) => <Text key={item.task_id} size="xs" c="orange">{item.reason} Задача не размещена — нужно изменить план или условия срока.</Text>)}
    <Group justify="space-between">
      <Tooltip multiline w={300} label={`${schedule?.rest_assumption ?? 'Время оценивается приблизительно'}${schedule?.start_at ? ` Начало: ${new Date(schedule.start_at).toLocaleString(undefined, { timeZone: schedule.timezone })}` : ''}`}><Text size="xs" c="dimmed">{calendarOnly ? 'Постоянное расписание' : finished ? 'Итоговый план' : 'План работы'}</Text></Tooltip>
      <Group gap={6}>{!finished && !calendarOnly && <Badge size="xs" variant="light" color="gray">Черновик</Badge>}<Text size="xs" fw={600}>{calendarOnly ? 'Учитывается до начала расчёта' : planned.length || schedule?.max_minutes != null ? `${duration(schedule?.max_minutes ?? planned.reduce((sum, task) => sum + task.plan.max_minutes, 0))} работы` : 'Оценивает время'}</Text></Group>
    </Group>
    {schedule?.timeline_error ? <Text size="xs" c="yellow">Шкала не построена: {schedule.timeline_error}. Порядок задач сохранён.</Text> : compact ? <div className="plan-agenda">{(expanded ? agenda : agenda.slice(0, 6)).map((block) => {
      const task = byId.get(block.task_id);
      const start = schedule?.start_at ? new Date(new Date(schedule.start_at).getTime() + block.start * 60000) : null;
      const label = block.title || task?.title || rest[block.kind] || 'Занято';
      return <a key={`${block.id}-${block.start}`} href={block.kind === 'task' && block.task_id ? `/?task=${block.task_id}` : undefined} className="plan-agenda-row" style={{ borderLeftColor: calendarColors[block.kind], background: calendarBlockColor(block.kind, !block.permanent) }}>
        <span>{start ? start.toLocaleString('ru-RU', { timeZone: zone, weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : `+${duration(block.start)}`}</span>
        <strong>{label}</strong><small>{duration(block.minutes)}</small>
      </a>;
    })}{!agenda.length && <Text size="xs" c="dimmed">{calendarOnly ? 'В календаре пока пусто' : 'План появится по мере расчёта'}</Text>}</div> : <div style={{ overflowX: 'auto', padding: '5px 0' }}>
      <div ref={chart} style={{ position: 'relative', minWidth: 760, height: (expanded ? visibleDays : 3) * 66 + 18 }}>
        {Array.from({ length: visibleDays }, (_, day) => <div key={day} style={{ position: 'absolute', top: day * 66, width: '100%' }}>
          <Text size="xs" c="dimmed">{dayLabel(day)}</Text>
          <div style={{ position: 'absolute', top: 24, width: '100%', height: 34, borderRadius: 0, background: 'var(--mantine-color-dark-7)' }} />
        </div>)}
        {!planned.length && !calendarOnly && <Text size="xs" c="dimmed" style={{ position: 'absolute', top: 32, left: 12 }}>Оценивает задачи — блоки появятся по мере расчёта</Text>}
        {slices.map((block) => {
          const task = byId.get(block.task_id);
          const label = block.title || task?.title || rest[block.kind] || 'Занято';
          return <Tooltip key={block.key} multiline w={270} label={`${label} · ${block.permanent ? 'Постоянный календарь' : 'План Ameba'} · ${duration(block.total_minutes)}${block.total_minutes !== block.minutes ? ' весь блок' : ''}${block.focus_minutes ? ` · концентрация ${block.focus_minutes} мин` : ''}${task?.plan.expected_result ? ` · ${task.plan.expected_result}` : ''}`}>
            <a data-block-key={block.key} className={`ameba-timeline-task ${block.kind !== 'task' ? 'ameba-timeline-rest' : ''}`} href={block.kind === 'task' && block.task_id ? `/?task=${block.task_id}` : undefined} aria-label={label} style={{ transformOrigin: 'left center', top: block.day * 66 + (block.kind === 'work' ? 60 : 24), height: block.kind === 'work' ? 6 : undefined, left: `${block.left}%`, width: `${block.width}%`, padding: block.width < 1 || block.kind === 'work' ? 0 : undefined, background: calendarBlockColor(block.kind, !block.permanent), border: block.permanent && block.width >= 0.5 ? `1px solid ${calendarColors[block.kind] ?? calendarColors.busy}` : 0 }}>{label}</a>
          </Tooltip>;
        })}
        <Group justify="space-between" style={{ position: 'absolute', bottom: 0, width: '100%' }}><Text size="xs" c="dimmed">{origin ? '00:00' : 'Начало условного дня'}</Text><Text size="xs" c="dimmed">24 ч</Text></Group>
      </div>
    </div>}
    {(compact ? agenda.length > 6 : days > 3) && <Button size="compact-xs" variant="subtle" onClick={() => setExpanded(!expanded)}>{expanded ? 'Свернуть' : compact ? 'Весь план' : `Все ${days} дней`}</Button>}
    <Group gap={6}><Badge size="xs" variant="light">Работа</Badge><Badge size="xs" color={calendarColors.sleep} variant="light">Сон</Badge><Badge size="xs" color={calendarColors.meal} variant="light">Еда</Badge><Badge size="xs" color={calendarColors.break} variant="light">Отдых</Badge><Badge size="xs" color={calendarColors.busy} variant="light">Календарь</Badge>{!schedule?.start_at && <Text size="xs" c="dimmed">Условные дни, не часы личного расписания</Text>}</Group>
    {schedule && <Group gap={6}>{Object.entries(schedule.potential_rewards ?? {}).map(([unit, value]) => <Badge key={unit} size="xs" variant="light">≈ {value} {unit}</Badge>)}</Group>}
    {tasks.length > planned.length && <Text size="xs" c="dimmed">Ещё {tasks.length - planned.length} в оценке</Text>}
  </Stack></Paper>;
}
