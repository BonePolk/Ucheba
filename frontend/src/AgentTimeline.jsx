import { useState } from 'react';
import { Alert, Badge, Button, Group, Menu, NumberInput, Paper, Stack, Text, Tooltip } from '@mantine/core';
import { calendarColors } from './calendarColors';

const LOAD_META = {
  free: { label: 'Свободно', color: 'gray', accent: '#454545' },
  light: { label: 'Лёгкая нагрузка', color: 'teal', accent: calendarColors.break },
  medium: { label: 'Средняя нагрузка', color: 'purple', accent: calendarColors.task },
  high: { label: 'Высокая нагрузка', color: 'orange', accent: '#c96f28' },
  overload: { label: 'Перегрузка', color: 'red', accent: '#c93b3b' },
};

const KIND_LABELS = {
  work: null,
  light: 'Облегчённый режим',
  recovery: 'Восстановление',
  free: 'Свободный день',
};

const OUTCOMES = [
  { value: 'done', label: 'Выполнено' },
  { value: 'partial', label: 'Частично' },
  { value: 'too_hard', label: 'Тяжело' },
  { value: 'missed', label: 'Не вышло' },
];

const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const MONTHS_LONG = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
const WEEKDAYS = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];

function parseIsoDate(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

function formatDateRange(startIso, endIso) {
  const start = parseIsoDate(startIso);
  const end = parseIsoDate(endIso);
  const sDay = start.getUTCDate();
  const eDay = end.getUTCDate();
  const sMon = start.getUTCMonth();
  const eMon = end.getUTCMonth();
  const sWd = WEEKDAYS[start.getUTCDay()];
  const eWd = WEEKDAYS[end.getUTCDay()];
  if (startIso === endIso) return `${sDay} ${MONTHS_LONG[sMon]} · ${sWd}`;
  if (sMon === eMon) return `${sDay}–${eDay} ${MONTHS_LONG[sMon]} · ${sWd}–${eWd}`;
  return `${sDay} ${MONTHS_SHORT[sMon]} – ${eDay} ${MONTHS_SHORT[eMon]} · ${sWd}–${eWd}`;
}

function formatShortDate(iso) {
  const d = parseIsoDate(iso);
  return `${d.getUTCDate()} ${MONTHS_SHORT[d.getUTCMonth()]}`;
}

function formatDeadline(iso, zone) {
  if (!iso) return null;
  const date = new Date(iso);
  return date.toLocaleString('ru-RU', { timeZone: zone || undefined, day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

function durationLabel(minutes) {
  if (!minutes) return '0 мин';
  if (minutes < 60) return `${minutes} мин`;
  const h = Math.round((minutes / 60) * 10) / 10;
  return `${h} ч`;
}

export default function AgentTimeline({ tasks = [], finished = true, schedule, calendarOnly = false, onFeedback, onAdjust, busy = false }) {
  const [expanded, setExpanded] = useState(false);
  const [feedbackDraft, setFeedbackDraft] = useState(null);
  const [customMinutes, setCustomMinutes] = useState('');

  const periods = schedule?.periods ?? [];
  const conflicts = schedule?.deadline_conflicts ?? [];
  const infeasibleConflicts = conflicts.filter((item) => item.infeasible);
  const tightConflicts = conflicts.filter((item) => !item.infeasible);
  const todayIso = schedule?.generated_date || new Date().toISOString().slice(0, 10);

  if (calendarOnly || !periods.length) {
    const previewDays = schedule?.days_summary ?? [];
    const estimated = tasks.filter((t) => t.plan);
    return (
      <Stack gap="xs" className="ameba-period-feed">
        {!finished && estimated.length > 0 && (
          <Stack gap={4}>
            <Text size="xs" c="dimmed">Предварительная оценка задач:</Text>
            {estimated.map((task) => (
              <Group key={task.id} justify="space-between" gap="xs">
                <Text size="xs" truncate style={{ flex: 1 }}>{task.title}</Text>
                <Badge size="xs" variant="light" color="purple">{task.plan.hours_label || task.plan.time_estimate}</Badge>
              </Group>
            ))}
          </Stack>
        )}
        {previewDays.length > 0 ? (
          <Stack gap={6}>
            <Group justify="space-between" wrap="wrap">
              <Text size="xs" c="dimmed">Расписание календаря на ближайшие дни</Text>
              <Text size="xs" c="dimmed">Обновите план для распределения нагрузки</Text>
            </Group>
            <div className="ameba-calendar-preview">
              {previewDays.slice(0, 7).map((day) => (
                <div key={day.date} className="ameba-calendar-preview-day">
                  <Text size="xs" fw={600}>{formatShortDate(day.date)} · {WEEKDAYS[parseIsoDate(day.date).getUTCDay()]}</Text>
                  <Text size="xs" c="dimmed">
                    {day.busy_minutes > 0 ? `Дела: ${durationLabel(day.busy_minutes)}` : 'Без плотной занятости'}
                  </Text>
                  {day.markers?.length > 0 && (
                    <Text size="xs" c="orange">{day.markers.map((m) => m.title).join(', ')}</Text>
                  )}
                </div>
              ))}
            </div>
          </Stack>
        ) : (
          <Text size="xs" c="dimmed">{finished ? 'Нажмите «Обновить план», чтобы построить периоды работы и отдыха.' : 'Оценивает задачи и доступную нагрузку…'}</Text>
        )}
      </Stack>
    );
  }

  const visiblePeriods = expanded ? periods : periods.slice(0, 5);
  const reservePct = schedule?.reserve_ratio != null ? Math.round(schedule.reserve_ratio * 100) : 22;

  function openFeedback(taskId, periodId, outcome) {
    setFeedbackDraft({ taskId, periodId, outcome });
    setCustomMinutes('');
  }

  function submitDraft(minutesOverride = null) {
    if (!feedbackDraft || !onFeedback) return;
    const raw = minutesOverride !== null ? minutesOverride : customMinutes === '' ? null : Number(customMinutes);
    onFeedback({
      task_id: feedbackDraft.taskId,
      period_id: feedbackDraft.periodId,
      outcome: feedbackDraft.outcome,
      actual_minutes: Number.isFinite(raw) && raw >= 0 ? raw : null,
    });
    setFeedbackDraft(null);
    setCustomMinutes('');
  }

  return (
    <Stack gap="sm" className="ameba-period-feed">
      <Group justify="space-between" wrap="wrap" gap="xs">
        <Group gap={6} wrap="wrap">
          <Badge size="xs" variant="light" color={!schedule.feasible ? 'orange' : tightConflicts.length ? 'yellow' : 'teal'}>
            {!schedule.feasible ? 'Есть невыполнимые сроки' : tightConflicts.length ? 'Плотный план' : 'План реалистичен'}
          </Badge>
          {!finished && <Badge size="xs" variant="light" color="gray">Черновик</Badge>}
          {schedule.hours_label && <Text size="xs" fw={600}>Всего работы: {schedule.hours_label}</Text>}
          <Text size="xs" c="dimmed">· резерв {reservePct}%</Text>
        </Group>
        {schedule.adaptation_summary && (
          <Tooltip multiline w={300} label={schedule.adaptation_summary}>
            <Text size="xs" c="dimmed" style={{ cursor: 'help' }}>Адаптация активна</Text>
          </Tooltip>
        )}
      </Group>

      {schedule.plan_changes?.length > 0 && (
        <Text size="xs" c="dimmed">Изменения: {schedule.plan_changes.join(' · ')}</Text>
      )}

      {infeasibleConflicts.length > 0 && (
        <Alert color="orange" variant="light" title="Недостаточно реалистичной ёмкости до дедлайнов">
          <Stack gap={6}>
            {infeasibleConflicts.map((item) => (
              <div key={item.task_id}>
                <Text size="xs" fw={600}>{item.reason}</Text>
                {item.options?.length > 0 && (
                  <Text size="xs" c="dimmed">Варианты: {item.options.join('; ')}.</Text>
                )}
              </div>
            ))}
          </Stack>
        </Alert>
      )}

      {tightConflicts.length > 0 && !infeasibleConflicts.length && (
        <Alert color="yellow" variant="light">
          <Stack gap={4}>
            {tightConflicts.map((item) => (
              <Text key={item.task_id} size="xs">{item.reason}</Text>
            ))}
          </Stack>
        </Alert>
      )}

      <div className="ameba-period-list">
        {visiblePeriods.map((period) => {
          const load = LOAD_META[period.load_level] || LOAD_META.medium;
          const isCurrent = period.start_date <= todayIso && todayIso <= period.end_date;
          const commitments = (period.days_summary ?? []).flatMap((day) =>
            (day.commitments ?? []).map((c) => ({ ...c, date: day.date })),
          );
          const markers = (period.days_summary ?? []).flatMap((day) =>
            (day.markers ?? []).map((m) => ({ ...m, date: day.date })),
          );
          const accent = period.kind === 'free' ? LOAD_META.free.accent : period.kind !== 'work' ? LOAD_META.light.accent : load.accent;

          return (
            <Paper
              key={period.id}
              withBorder
              radius="md"
              p="sm"
              className={`ameba-period-card ${isCurrent ? 'is-current' : ''}`}
              style={{ borderLeft: `3px solid ${accent}` }}
            >
              <Stack gap="xs">
                <Group justify="space-between" align="flex-start" wrap="wrap" gap="xs">
                  <Group gap={6} wrap="wrap">
                    <Text fw={700} size="sm">{formatDateRange(period.start_date, period.end_date)}</Text>
                    {isCurrent && <Badge size="xs" variant="filled" color="purple">Сейчас</Badge>}
                    {KIND_LABELS[period.kind] && (
                      <Badge size="xs" variant="light" color="teal">{KIND_LABELS[period.kind]}</Badge>
                    )}
                  </Group>
                  <Group gap={6} wrap="wrap">
                    <Badge size="xs" variant="light" color={load.color}>{load.label}</Badge>
                    {period.kind === 'work' && (
                      <Text size="xs" fw={600} c="dimmed">{period.hours_label}</Text>
                    )}
                    {onAdjust && (
                      <Menu position="bottom-end" withinPortal>
                        <Menu.Target>
                          <Button size="compact-xs" variant="subtle" color="gray" disabled={busy}>Режим дней</Button>
                        </Menu.Target>
                        <Menu.Dropdown>
                          <Menu.Label>{formatDateRange(period.start_date, period.end_date)}</Menu.Label>
                          <Menu.Item onClick={() => onAdjust({ day_overrides: Object.fromEntries((period.days_summary ?? []).map((d) => [d.date, 'light'])) })}>
                            Сделать {period.start_date === period.end_date ? 'облегчённым' : 'эти дни облегчёнными'}
                          </Menu.Item>
                          <Menu.Item onClick={() => onAdjust({ day_overrides: Object.fromEntries((period.days_summary ?? []).map((d) => [d.date, 'free'])) })}>
                            Оставить {period.start_date === period.end_date ? 'выходным' : 'эти дни выходными'}
                          </Menu.Item>
                          <Menu.Item onClick={() => onAdjust({ day_overrides: Object.fromEntries((period.days_summary ?? []).map((d) => [d.date, 'auto'])) })}>
                            Обычный расчёт
                          </Menu.Item>
                        </Menu.Dropdown>
                      </Menu>
                    )}
                  </Group>
                </Group>

                <Text size="sm" fw={600}>
                  {period.kind === 'work' ? `Цель: ${period.title}` : period.title}
                </Text>

                {period.tasks?.length > 0 && (
                  <Stack gap={6} className="ameba-period-tasks">
                    {period.tasks.map((task) => {
                      const editingThis = feedbackDraft?.taskId === task.task_id && feedbackDraft?.periodId === period.id;
                      const dlText = formatDeadline(task.deadline, schedule.timezone);
                      return (
                        <div key={task.task_id} className="ameba-period-task">
                          <Group justify="space-between" align="flex-start" wrap="wrap" gap="xs">
                            <div style={{ flex: 1, minWidth: 180 }}>
                              <Group gap={6} wrap="wrap">
                                <Text component="a" href={`/?task=${task.task_id}`} size="sm" fw={500} c="inherit" td="none">
                                  {task.title}
                                </Text>
                                <Badge size="xs" variant="outline" color="purple">{task.hours_label}</Badge>
                                {!task.completes_task && (
                                  <Text size="xs" c="dimmed">≈ {task.share_percent}% от остатка</Text>
                                )}
                              </Group>
                              {task.goal && (
                                <Text size="xs" c="dimmed">{task.goal}</Text>
                              )}
                              {dlText && (
                                <Text size="xs" c={task.deadline_policy === 'soft' ? 'dimmed' : 'orange'}>
                                  Срок: {dlText}{task.deadline_policy === 'expires' ? ' (строго)' : ''}
                                </Text>
                              )}
                            </div>
                            {(onFeedback || onAdjust) && (
                              <Group gap={4} wrap="wrap">
                                {onAdjust && (
                                  <>
                                    <Tooltip label="Сдвинуть раньше">
                                      <Button size="compact-xs" variant="subtle" color="gray" disabled={busy}
                                        onClick={() => onAdjust({ task_pin: { task_id: task.task_id, shift_days: -1 } })}>
                                        ←
                                      </Button>
                                    </Tooltip>
                                    <Tooltip label="Сдвинуть позже">
                                      <Button size="compact-xs" variant="subtle" color="gray" disabled={busy}
                                        onClick={() => onAdjust({ task_pin: { task_id: task.task_id, shift_days: 1 } })}>
                                        →
                                      </Button>
                                    </Tooltip>
                                  </>
                                )}
                                {onFeedback && (
                                  <Menu position="bottom-end" withinPortal>
                                    <Menu.Target>
                                      <Button size="compact-xs" variant="light" color="purple" disabled={busy}>Отклик</Button>
                                    </Menu.Target>
                                    <Menu.Dropdown>
                                      {OUTCOMES.map((item) => (
                                        <Menu.Item key={item.value} onClick={() => openFeedback(task.task_id, period.id, item.value)}>
                                          {item.label}
                                        </Menu.Item>
                                      ))}
                                    </Menu.Dropdown>
                                  </Menu>
                                )}
                              </Group>
                            )}
                          </Group>
                          {editingThis && (
                            <Group gap="xs" mt={6} wrap="wrap" className="ameba-feedback-bar">
                              <Text size="xs" fw={500}>
                                {OUTCOMES.find((o) => o.value === feedbackDraft.outcome)?.label}:
                              </Text>
                              <NumberInput
                                size="xs"
                                w={120}
                                min={0}
                                max={1440}
                                placeholder="Мин (необяз.)"
                                value={customMinutes}
                                onChange={setCustomMinutes}
                              />
                              <Button size="compact-xs" variant="subtle" onClick={() => submitDraft(30)}>+30м</Button>
                              <Button size="compact-xs" variant="subtle" onClick={() => submitDraft(60)}>+1ч</Button>
                              <Button size="compact-xs" variant="subtle" onClick={() => submitDraft(120)}>+2ч</Button>
                              <Button size="compact-xs" color="purple" loading={busy} onClick={() => submitDraft()}>Сохранить</Button>
                              <Button size="compact-xs" variant="subtle" color="gray" onClick={() => setFeedbackDraft(null)}>Отмена</Button>
                            </Group>
                          )}
                        </div>
                      );
                    })}
                  </Stack>
                )}

                {period.day_hints?.length > 0 && (
                  <Stack gap={2}>
                    {period.day_hints.map((hint, idx) => (
                      <Text key={idx} size="xs" c="dimmed">• {hint}</Text>
                    ))}
                  </Stack>
                )}

                {period.rest_note && (
                  <Text size="xs" c="teal">Отдых: {period.rest_note}</Text>
                )}

                {period.risks?.length > 0 && (
                  <Stack gap={2}>
                    {period.risks.map((risk, idx) => (
                      <Text key={idx} size="xs" c="orange">Риск: {risk}</Text>
                    ))}
                  </Stack>
                )}

                {markers.length > 0 && (
                  <Text size="xs" c="dimmed">
                    События-ориентиры в календаре: {markers.map((m) => `${formatShortDate(m.date)} — ${m.title}`).join('; ')}
                  </Text>
                )}

                {commitments.length > 0 && (
                  <details className="ameba-commitments">
                    <summary>
                      Обязательные события календаря ({commitments.length}) · {durationLabel(period.days_summary.reduce((s, d) => s + (d.busy_minutes || 0), 0))}
                    </summary>
                    <Stack gap={3} mt={6}>
                      {commitments.map((c, idx) => (
                        <Text key={`${c.id}-${c.date}-${idx}`} size="xs" c="dimmed">
                          {formatShortDate(c.date)} · {c.all_day ? 'Весь день' : `${c.start}–${c.end}`} — {c.title}
                        </Text>
                      ))}
                    </Stack>
                  </details>
                )}
              </Stack>
            </Paper>
          );
        })}
      </div>

      {periods.length > 5 && (
        <Button size="compact-xs" variant="subtle" onClick={() => setExpanded(!expanded)}>
          {expanded ? 'Свернуть' : `Показать все периоды (${periods.length})`}
        </Button>
      )}
    </Stack>
  );
}
