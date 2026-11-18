import { useState } from 'react';
import {
  CalendarDays,
  ChevronDown,
  ChevronRight,
  MoreHorizontal,
  SlidersHorizontal,
} from 'lucide-react';
import { calendarColors } from './calendarColors';
import { Button, IconButton } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Badge, EmptyState, Notice } from '@/components/ui/surface';
import { HintTooltip } from '@/components/ui/tooltip';
import { CollapseSection, LAYOUT_TRANSITION, motion } from '@/lib/motion';

const LOAD_META = {
  free: { label: 'Свободно', variant: 'default', accent: '#454545' },
  light: { label: 'Лёгкая', variant: 'success', accent: calendarColors.break },
  medium: { label: 'Средняя', variant: 'accent', accent: calendarColors.task },
  high: { label: 'Высокая', variant: 'warning', accent: '#c96f28' },
  overload: { label: 'Перегрузка', variant: 'danger', accent: '#c93b3b' },
};

const KIND_LABELS = {
  work: null,
  light: 'Облегчённый',
  recovery: 'Восстановление',
  free: 'Выходной',
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

export function parseIsoDate(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

export function addIsoDays(iso, offset) {
  const dt = parseIsoDate(iso);
  dt.setUTCDate(dt.getUTCDate() + offset);
  return dt.toISOString().slice(0, 10);
}

export function formatDateRange(startIso, endIso) {
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

export function formatShortDate(iso) {
  const d = parseIsoDate(iso);
  return `${d.getUTCDate()} ${MONTHS_SHORT[d.getUTCMonth()]}`;
}

function formatDeadline(iso, zone) {
  if (!iso) return null;
  const date = new Date(iso);
  return date.toLocaleString('ru-RU', {
    timeZone: zone || undefined,
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function durationLabel(minutes) {
  if (!minutes) return '0 мин';
  if (minutes < 60) return `${minutes} мин`;
  const h = Math.round((minutes / 60) * 10) / 10;
  return `${h} ч`;
}

export default function AgentTimeline({
  tasks = [],
  finished = true,
  schedule,
  calendarOnly = false,
  onFeedback,
  onAdjust,
  busy = false,
  taskPins = {},
  estimateOverrides = {},
  onRefresh,
  hideConflicts = false,
}) {
  const [expanded, setExpanded] = useState(false);
  const [changesOpen, setChangesOpen] = useState(false);
  const [openDetails, setOpenDetails] = useState({});
  const [feedbackDraft, setFeedbackDraft] = useState(null);
  const [customMinutes, setCustomMinutes] = useState('');
  const [estimateDraft, setEstimateDraft] = useState(null);
  const [estimateInput, setEstimateInput] = useState('');

  const periods = schedule?.periods ?? [];
  const conflicts = schedule?.deadline_conflicts ?? [];
  const infeasibleConflicts = conflicts.filter((item) => item.infeasible);
  const tightConflicts = conflicts.filter((item) => !item.infeasible);
  const todayIso = schedule?.generated_date || new Date().toISOString().slice(0, 10);
  const tomorrowIso = addIsoDays(todayIso, 1);

  if (calendarOnly || !periods.length) {
    const previewDays = schedule?.days_summary ?? [];
    const estimated = tasks.filter((t) => t.plan);
    return (
      <div className="flex flex-col gap-3">
        {!finished && estimated.length > 0 && (
          <div className="border border-[#303030] bg-[#111111] p-3 flex flex-col gap-1.5">
            <div className="text-[12px] font-medium text-[#929292]">Предварительная оценка</div>
            <div className="divide-y divide-[#1E1E1E]">
              {estimated.map((task) => (
                <div key={task.id} className="flex items-center justify-between gap-2 py-1 text-[12px]">
                  <span className="truncate text-[#EAEAEA]">{task.title}</span>
                  <Badge variant="accent">{task.plan.hours_label || task.plan.time_estimate}</Badge>
                </div>
              ))}
            </div>
          </div>
        )}

        {previewDays.length > 0 ? (
          <div className="border border-[#303030] bg-[#111111] p-3 flex flex-col gap-2">
            <span className="text-[12px] font-medium text-[#EAEAEA]">Ближайшие дни</span>
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2">
              {previewDays.slice(0, 7).map((day) => (
                <div key={day.date} className="border border-[#262626] bg-[#0D0D0D] px-2.5 py-2 flex flex-col gap-0.5">
                  <div className="text-[12px] font-semibold text-[#EAEAEA]">
                    {formatShortDate(day.date)} · {WEEKDAYS[parseIsoDate(day.date).getUTCDay()]}
                  </div>
                  <div className="text-[11px] text-[#929292]">
                    {day.busy_minutes > 0 ? durationLabel(day.busy_minutes) : 'Свободно'}
                  </div>
                  {day.markers?.length > 0 && (
                    <div className="text-[11px] text-[#D4A24C] truncate">{day.markers.map((m) => m.title).join(', ')}</div>
                  )}
                </div>
              ))}
            </div>
          </div>
        ) : (
          <EmptyState
            icon={<CalendarDays className="h-5 w-5" />}
            title={finished ? 'План не построен' : 'Ameba оценивает задачи…'}
            action={finished && onRefresh ? <Button variant="primary" onClick={onRefresh}>Обновить план</Button> : undefined}
          />
        )}
      </div>
    );
  }

  const visiblePeriods = expanded ? periods : periods.slice(0, 6);
  const planChanges = schedule.plan_changes ?? [];

  function openFeedback(taskId, periodId, outcome) {
    setEstimateDraft(null);
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

  function openEstimateEditor(task, periodId, manualVal) {
    setFeedbackDraft(null);
    setEstimateDraft({ taskId: task.task_id, periodId });
    setEstimateInput(manualVal !== undefined ? String(manualVal) : String(task.minutes ?? ''));
  }

  function toggleDetails(periodId) {
    setOpenDetails((prev) => ({ ...prev, [periodId]: !prev[periodId] }));
  }

  function relativeBadge(period) {
    if (period.start_date <= todayIso && todayIso <= period.end_date) return 'Сейчас';
    if (period.start_date === tomorrowIso && period.end_date === tomorrowIso) return 'Завтра';
    return null;
  }

  return (
    <div className="flex flex-col gap-3">
      {!hideConflicts && infeasibleConflicts.length > 0 && (
        <Notice tone="warning" title="Недостаточно времени до сроков">
          <div className="flex flex-col gap-1.5 mt-1">
            {infeasibleConflicts.map((item) => (
              <div key={item.task_id}>
                <div className="font-medium text-[#EAEAEA]">{item.reason}</div>
                {item.options?.length > 0 && (
                  <div className="text-[11px] text-[#929292]">{item.options.join('; ')}</div>
                )}
              </div>
            ))}
          </div>
        </Notice>
      )}

      {!hideConflicts && tightConflicts.length > 0 && !infeasibleConflicts.length && (
        <Notice tone="warning">
          <div className="flex flex-col gap-1">
            {tightConflicts.map((item) => (
              <div key={item.task_id}>{item.reason}</div>
            ))}
          </div>
        </Notice>
      )}

      {planChanges.length > 0 && (
        <div className="border border-[#242424] bg-[#0D0D0D] px-3 py-1.5 text-[12px]">
          {planChanges.length === 1 ? (
            <div className="text-[#929292] truncate">
              <span className="text-[#EAEAEA] font-medium">Изменение: </span>
              {planChanges[0]}
            </div>
          ) : (
            <div>
              <button
                type="button"
                onClick={() => setChangesOpen(!changesOpen)}
                aria-expanded={changesOpen}
                className="inline-flex items-center gap-1.5 text-[#929292] hover:text-[#EAEAEA] cursor-pointer"
              >
                {changesOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                <span>Изменения ({planChanges.length})</span>
              </button>
              <CollapseSection open={changesOpen}>
                <ul className="list-disc pl-4 m-0 mt-1 space-y-0.5 text-[#929292]">
                  {planChanges.map((change, i) => <li key={i}>{change}</li>)}
                </ul>
              </CollapseSection>
            </div>
          )}
        </div>
      )}

      {/* Dense vertical period feed */}
      <div className="border border-[#303030] bg-[#111111] divide-y divide-[#242424]">
        {visiblePeriods.map((period) => {
          const load = LOAD_META[period.load_level] || LOAD_META.medium;
          const isCurrent = period.start_date <= todayIso && todayIso <= period.end_date;
          const rel = relativeBadge(period);
          const commitments = (period.days_summary ?? []).flatMap((day) =>
            (day.commitments ?? []).map((c) => ({ ...c, date: day.date })),
          );
          const markers = (period.days_summary ?? []).flatMap((day) =>
            (day.markers ?? []).map((m) => ({ ...m, date: day.date })),
          );
          const accent = period.kind === 'free' ? LOAD_META.free.accent : period.kind !== 'work' ? LOAD_META.light.accent : load.accent;
          const detailsVisible = Boolean(openDetails[period.id]);
          const hasDetails = Boolean(period.day_hints?.length || period.rest_note || markers.length || commitments.length);

          return (
            <motion.section
              key={period.id}
              layout="position"
              transition={LAYOUT_TRANSITION}
              className="grid grid-cols-1 md:grid-cols-[170px_minmax(0,1fr)] gap-2.5 px-3 py-2.5 border-l-2"
              style={{
                borderLeftColor: accent,
                background: isCurrent ? '#141118' : undefined,
              }}
            >
              {/* Left period date/load rail */}
              <div className="flex flex-row md:flex-col items-center md:items-start justify-between md:justify-start gap-1.5 min-w-0">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-[13px] font-semibold text-[#EAEAEA]">
                    {formatDateRange(period.start_date, period.end_date)}
                  </span>
                  {rel && <Badge variant={isCurrent ? 'accent' : 'outline'}>{rel}</Badge>}
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant={load.variant}>{load.label}</Badge>
                  {period.kind === 'work' && (
                    <span className="text-[12px] font-medium text-[#B8B8B8] tabular-nums">{period.hours_label}</span>
                  )}
                </div>
                {KIND_LABELS[period.kind] && (
                  <span className="text-[11px] text-[#6EBE86]">{KIND_LABELS[period.kind]}</span>
                )}
              </div>

              {/* Right period focus & tasks */}
              <div className="min-w-0 flex flex-col gap-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="text-[13px] font-semibold text-[#EAEAEA] min-w-0 break-words">
                    {period.title}
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    {hasDetails && (
                      <Button
                        size="xs"
                        variant="ghost"
                        aria-expanded={detailsVisible}
                        onClick={() => toggleDetails(period.id)}
                      >
                        {detailsVisible ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                        <span>Детали</span>
                      </Button>
                    )}
                    {onAdjust && (
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button size="xs" variant="outline" disabled={busy} leftIcon={<SlidersHorizontal className="h-3 w-3" />}>
                            Режим
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent>
                          <DropdownMenuLabel>{formatDateRange(period.start_date, period.end_date)}</DropdownMenuLabel>
                          <DropdownMenuItem
                            onClick={() => onAdjust({
                              day_overrides: Object.fromEntries((period.days_summary ?? []).map((d) => [d.date, 'light'])),
                            })}
                          >
                            Облегчённый
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onClick={() => onAdjust({
                              day_overrides: Object.fromEntries((period.days_summary ?? []).map((d) => [d.date, 'focused'])),
                            })}
                          >
                            Фокусный
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onClick={() => onAdjust({
                              day_overrides: Object.fromEntries((period.days_summary ?? []).map((d) => [d.date, 'free'])),
                            })}
                          >
                            Выходной
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onClick={() => onAdjust({
                              day_overrides: Object.fromEntries((period.days_summary ?? []).map((d) => [d.date, 'auto'])),
                            })}
                          >
                            Авто
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    )}
                  </div>
                </div>

                {period.tasks?.length > 0 && (
                  <div className="flex flex-col divide-y divide-[#1E1E1E] border border-[#242424] bg-[#0D0D0D]">
                    {period.tasks.map((task) => {
                      const editingThis = feedbackDraft?.taskId === task.task_id && feedbackDraft?.periodId === period.id;
                      const editingEstThis = estimateDraft?.taskId === task.task_id && estimateDraft?.periodId === period.id;
                      const dlText = formatDeadline(task.deadline, schedule.timezone);
                      const isPinned = Boolean(taskPins[String(task.task_id)]);
                      const manualVal = estimateOverrides[String(task.task_id)];

                      return (
                        <div key={task.task_id} className="px-2.5 py-1.5 flex flex-col gap-1.5">
                          <div className="flex flex-wrap items-start justify-between gap-2">
                            <div className="min-w-0 flex-1">
                              <div className="flex flex-wrap items-center gap-1.5">
                                <a href={`/?task=${task.task_id}`} className="text-[13px] font-medium text-[#EAEAEA] hover:text-[#CFA9F5] hover:underline">
                                  {task.title}
                                </a>
                                <Badge variant="accent" className="tabular-nums">{task.hours_label}</Badge>
                                {!task.completes_task && (
                                  <span className="text-[11px] text-[#929292] tabular-nums">{task.share_percent}%</span>
                                )}
                                {isPinned && <Badge variant="info">Сдвиг</Badge>}
                                {manualVal !== undefined && <Badge variant="info">Ост. {manualVal}м</Badge>}
                                {dlText && (
                                  <span className={`text-[11px] tabular-nums ${task.deadline_policy === 'soft' ? 'text-[#929292]' : 'text-[#D4A24C]'}`}>
                                    до {dlText}{task.deadline_policy === 'expires' ? ' (строго)' : ''}
                                  </span>
                                )}
                              </div>
                              {task.goal && (
                                <HintTooltip label={task.goal}>
                                  <div className="text-[11px] text-[#929292] mt-0.5 line-clamp-1">{task.goal}</div>
                                </HintTooltip>
                              )}
                            </div>

                            {(onFeedback || onAdjust) && (
                              <div className="flex items-center gap-1 shrink-0">
                                {onFeedback && (
                                  <DropdownMenu>
                                    <DropdownMenuTrigger asChild>
                                      <Button size="xs" variant="accent-subtle" disabled={busy}>Отклик</Button>
                                    </DropdownMenuTrigger>
                                    <DropdownMenuContent>
                                      {OUTCOMES.map((item) => (
                                        <DropdownMenuItem key={item.value} onClick={() => openFeedback(task.task_id, period.id, item.value)}>
                                          {item.label}
                                        </DropdownMenuItem>
                                      ))}
                                    </DropdownMenuContent>
                                  </DropdownMenu>
                                )}
                                {onAdjust && (
                                  <DropdownMenu>
                                    <DropdownMenuTrigger asChild>
                                      <IconButton size="icon-sm" variant="ghost" aria-label={`Корректировка: ${task.title}`} disabled={busy}>
                                        <MoreHorizontal className="h-3.5 w-3.5" />
                                      </IconButton>
                                    </DropdownMenuTrigger>
                                    <DropdownMenuContent>
                                      <DropdownMenuItem onClick={() => onAdjust({ task_pin: { task_id: task.task_id, shift_days: -1 } })}>
                                        Сдвинуть раньше (−1 д)
                                      </DropdownMenuItem>
                                      <DropdownMenuItem onClick={() => onAdjust({ task_pin: { task_id: task.task_id, shift_days: 1 } })}>
                                        Сдвинуть позже (+1 д)
                                      </DropdownMenuItem>
                                      {isPinned && (
                                        <DropdownMenuItem onClick={() => onAdjust({ task_pin: { task_id: task.task_id, clear: true } })}>
                                          Сбросить сдвиг
                                        </DropdownMenuItem>
                                      )}
                                      <DropdownMenuSeparator />
                                      <DropdownMenuItem onClick={() => openEstimateEditor(task, period.id, manualVal)}>
                                        Оценка остатка…
                                      </DropdownMenuItem>
                                      {manualVal !== undefined && (
                                        <DropdownMenuItem onClick={() => onAdjust({ estimate_override: { task_id: task.task_id, remaining_minutes: null } })}>
                                          Сбросить оценку
                                        </DropdownMenuItem>
                                      )}
                                    </DropdownMenuContent>
                                  </DropdownMenu>
                                )}
                              </div>
                            )}
                          </div>

                          <CollapseSection open={editingThis}>
                            {editingThis && (
                              <div className="flex flex-wrap items-center gap-1.5 border-t border-dashed border-[#303030] pt-1.5">
                                <span className="text-[12px] font-medium text-[#EAEAEA]">
                                  {OUTCOMES.find((o) => o.value === feedbackDraft.outcome)?.label}:
                                </span>
                                <Input
                                  type="number"
                                  min={0}
                                  max={1440}
                                  placeholder="Мин"
                                  value={customMinutes}
                                  onChange={(event) => setCustomMinutes(event.currentTarget.value)}
                                  className="w-[90px] h-[25px] text-[12px]"
                                />
                                <Button size="xs" variant="outline" onClick={() => submitDraft(30)}>+30м</Button>
                                <Button size="xs" variant="outline" onClick={() => submitDraft(60)}>+1ч</Button>
                                <Button size="xs" variant="outline" onClick={() => submitDraft(120)}>+2ч</Button>
                                <Button size="xs" variant="primary" loading={busy} onClick={() => submitDraft()}>OK</Button>
                                <Button size="xs" variant="ghost" onClick={() => setFeedbackDraft(null)}>✕</Button>
                              </div>
                            )}
                          </CollapseSection>

                          <CollapseSection open={editingEstThis}>
                            {editingEstThis && (
                              <div className="flex flex-wrap items-center gap-1.5 border-t border-dashed border-[#303030] pt-1.5">
                                <span className="text-[12px] text-[#929292]">Остаток (мин):</span>
                                <Input
                                  type="number"
                                  min={0}
                                  max={10000}
                                  placeholder="Мин"
                                  value={estimateInput}
                                  onChange={(event) => setEstimateInput(event.currentTarget.value)}
                                  className="w-[90px] h-[25px] text-[12px]"
                                />
                                <Button
                                  size="xs"
                                  variant="primary"
                                  disabled={busy}
                                  onClick={() => {
                                    const val = estimateInput.trim() === '' ? null : Math.max(0, Number(estimateInput));
                                    onAdjust?.({ estimate_override: { task_id: task.task_id, remaining_minutes: val } });
                                    setEstimateDraft(null);
                                  }}
                                >
                                  OK
                                </Button>
                                <Button size="xs" variant="ghost" onClick={() => setEstimateDraft(null)}>✕</Button>
                              </div>
                            )}
                          </CollapseSection>
                        </div>
                      );
                    })}
                  </div>
                )}

                {period.risks?.length > 0 && (
                  <div className="flex flex-col gap-0.5">
                    {period.risks.map((risk, idx) => (
                      <div key={idx} className="text-[11px] text-[#D4A24C]">{risk}</div>
                    ))}
                  </div>
                )}

                <CollapseSection open={detailsVisible}>
                  <div className="border-t border-[#242424] pt-2 flex flex-col gap-1.5 text-[12px]">
                    {period.day_hints?.map((hint, idx) => (
                      <div key={idx} className="text-[#B8B8B8]">• {hint}</div>
                    ))}
                    {period.rest_note && (
                      <div className="text-[#6EBE86]">{period.rest_note}</div>
                    )}
                    {markers.length > 0 && (
                      <div className="text-[#D4A24C]">
                        {markers.map((m) => `${formatShortDate(m.date)} — ${m.title}`).join('; ')}
                      </div>
                    )}
                    {commitments.length > 0 && (
                      <div className="flex flex-col gap-0.5 border-l border-[#303030] pl-2">
                        <span className="text-[11px] text-[#929292]">
                          Календарь ({commitments.length}) · {durationLabel(period.days_summary.reduce((s, d) => s + (d.busy_minutes || 0), 0))}
                        </span>
                        {commitments.map((c, idx) => (
                          <div key={`${c.id}-${c.date}-${idx}`} className="text-[11px] text-[#929292] tabular-nums">
                            {formatShortDate(c.date)} · {c.all_day ? 'Весь день' : `${c.start}–${c.end}`} — {c.title}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </CollapseSection>
              </div>
            </motion.section>
          );
        })}
      </div>

      {periods.length > 6 && (
        <div>
          <Button size="sm" variant="subtle" onClick={() => setExpanded(!expanded)}>
            {expanded ? 'Свернуть' : `Все периоды (${periods.length})`}
          </Button>
        </div>
      )}
    </div>
  );
}
