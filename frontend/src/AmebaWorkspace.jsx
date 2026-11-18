import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowRight,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  MoreHorizontal,
  RefreshCw,
  RotateCcw,
  Terminal,
  Trash2,
  X,
} from 'lucide-react';
import { agentRequest, request } from './api';
import AgentTimeline, { addIsoDays, durationLabel, formatShortDate, parseIsoDate } from './AgentTimeline';
import AgentProgress from './AgentProgress';
import { DeadlineRisk } from './RankingAssessment';
import { calendarBlockColor, calendarColors } from './calendarColors';
import { Button, IconButton } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input, Textarea } from '@/components/ui/input';
import { SelectField } from '@/components/ui/select';
import { Badge, EmptyState, Notice, PageHeader, PageLoader, SectionHeader, StatusIndicator } from '@/components/ui/surface';
import { Table, TableBody, TableCell, TableContainer, TableHead, TableHeader, TableRow, tableRowClass } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { HintTooltip } from '@/components/ui/tooltip';
import { CollapseSection, LAYOUT_TRANSITION, motion, TabTransition } from '@/lib/motion';

const WEEKDAYS_RU = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
const DAY_MODES = [
  { value: 'auto', label: 'Авто' },
  { value: 'normal', label: 'Обычный' },
  { value: 'light', label: 'Облегчённый' },
  { value: 'focused', label: 'Фокусный' },
  { value: 'free', label: 'Выходной' },
];
const LOAD_LABELS = {
  free: { label: 'Свободно', variant: 'default' },
  light: { label: 'Лёгкая', variant: 'success' },
  medium: { label: 'Средняя', variant: 'accent' },
  high: { label: 'Высокая', variant: 'warning' },
  overload: { label: 'Перегрузка', variant: 'danger' },
};

function mondayIso(iso) {
  const dt = parseIsoDate(iso);
  const diff = (dt.getUTCDay() + 6) % 7;
  return addIsoDays(iso, -diff);
}

function toMin(hhmm) {
  const [h, m] = String(hhmm || '00:00').split(':').map(Number);
  return (h || 0) * 60 + (m || 0);
}

function layoutOverlaps(events) {
  const groups = [];
  for (const event of [...events].sort((a, b) => a.start.localeCompare(b.start))) {
    let group = groups.at(-1);
    if (!group || event.start >= group.end) {
      group = { end: event.end, events: [], lanes: [] };
      groups.push(group);
    }
    let lane = group.lanes.findIndex((end) => end <= event.start);
    if (lane < 0) lane = group.lanes.length;
    group.lanes[lane] = event.end;
    group.end = group.end > event.end ? group.end : event.end;
    group.events.push({ ...event, lane });
  }
  return groups.flatMap((group) => group.events.map((event) => ({ ...event, columns: group.lanes.length })));
}

function resolvePlanStatus({ rankingData, liveActive, isRunning, rankError, failedRun }) {
  if (isRunning || liveActive) return { tone: 'active', pulse: true, badge: 'accent', label: 'Расчёт' };
  if (rankError || failedRun) return { tone: 'danger', pulse: false, badge: 'danger', label: 'Ошибка' };
  if (!rankingData?.has_plan || rankingData?.plan_reset) return { tone: 'idle', pulse: false, badge: 'outline', label: 'Нет плана' };
  const conflicts = rankingData?.schedule?.deadline_conflicts ?? [];
  const unfitted = (rankingData?.schedule?.remaining ?? []).some((r) => r.remaining_work_minutes > 0);
  if (!rankingData?.schedule?.feasible || conflicts.length > 0 || unfitted) {
    return { tone: 'warning', pulse: false, badge: 'warning', label: rankingData.needs_refresh ? 'Устарел · риски' : 'Есть риски' };
  }
  if (rankingData?.needs_refresh) return { tone: 'warning', pulse: false, badge: 'warning', label: 'Устарел' };
  return { tone: 'success', pulse: false, badge: 'success', label: 'Готов' };
}

export function AmebaSummaryBar({ ranking, queue, rank, livePlan, onRefresh }) {
  const data = ranking?.data;
  const liveActive = Boolean(livePlan?.data?.running || (data?.has_plan && livePlan?.data?.started_at && livePlan.data.started_at > (data?.generated_at ?? '')));
  const activeSchedule = (liveActive ? livePlan?.data?.schedule : null) ?? (data?.plan_reset ? null : data?.schedule);
  const periods = activeSchedule?.periods ?? [];
  const conflicts = activeSchedule?.deadline_conflicts ?? [];
  const isRunning = Boolean(queue?.data?.ameba_running || rank?.isPending);
  const status = resolvePlanStatus({
    rankingData: data,
    liveActive,
    isRunning,
    rankError: rank?.error,
    failedRun: false,
  });
  const reservePct = activeSchedule?.reserve_ratio != null ? Math.round(activeSchedule.reserve_ratio * 100) : null;
  const focusPeriod = periods.find((p) => p.kind === 'work') ?? periods[0];
  const topTaskId = data?.task_ids?.[0];
  const topTaskReason = data?.reasons?.find((r) => r.task_id === topTaskId);

  return (
    <div className="border border-[#303030] bg-[#111111] px-3 py-2 flex flex-wrap items-center justify-between gap-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 min-w-0 text-[12px]">
        <div className="flex items-center gap-2 shrink-0">
          <StatusIndicator tone={status.tone} pulse={status.pulse} />
          <span className="font-semibold text-[#EAEAEA]">Ameba</span>
          <Badge variant={status.badge}>{status.label}</Badge>
        </div>

        {activeSchedule?.hours_label && (
          <strong className="font-medium text-[#EAEAEA] tabular-nums">{activeSchedule.hours_label}</strong>
        )}

        {reservePct != null && (
          <span className="text-[#929292] tabular-nums">резерв {reservePct}%</span>
        )}

        {data?.pending_count > 0 && (
          <span className="text-[#D4A24C] tabular-nums">Monocle: {data.pending_count}</span>
        )}

        {conflicts.length > 0 && (
          <HintTooltip label={conflicts.map((c) => c.reason).join('\n')}>
            <Badge variant="warning" className="cursor-help">
              <AlertTriangle className="h-3 w-3" />
              Риски: {conflicts.length}
            </Badge>
          </HintTooltip>
        )}

        {focusPeriod ? (
          <span className="text-[#B8B8B8] truncate max-w-[240px]">{focusPeriod.title}</span>
        ) : topTaskReason ? (
          <span className="text-[#929292] truncate max-w-[200px]">{topTaskReason.time_estimate}</span>
        ) : null}
      </div>

      <div className="flex items-center gap-1.5 shrink-0">
        {rank?.data?.unchanged && (
          <span className="text-[11px] text-[#929292] hidden xl:inline">Без изменений</span>
        )}
        <Button
          size="sm"
          variant="default"
          leftIcon={<RefreshCw className={`h-3 w-3 ${isRunning ? 'animate-spin' : ''}`} />}
          loading={rank?.isPending && !queue?.data?.ameba_running}
          disabled={queue?.data?.browser_busy}
          onClick={onRefresh}
        >
          {queue?.data?.ameba_running ? 'Применить' : 'Обновить'}
        </Button>
        <Button size="sm" variant="accent-subtle" asChild>
          <a href="/ameba">
            План
            <ArrowRight className="h-3 w-3" />
          </a>
        </Button>
      </div>
    </div>
  );
}

function LoadAnalysisTab({ daily = [], previewDays = [], onAdjust, busy }) {
  const [windowRange, setWindowRange] = useState('week1');
  const [selectedDate, setSelectedDate] = useState(null);
  const sourceDays = daily.length
    ? daily
    : previewDays.map((d) => ({
      date: d.date,
      weekday: d.weekday,
      mode: 'normal',
      load_level: d.busy_minutes >= 240 ? 'high' : d.busy_minutes > 0 ? 'medium' : 'free',
      busy_minutes: d.busy_minutes ?? 0,
      physical_free_minutes: Math.max(0, 900 - (d.busy_minutes ?? 0)),
      realistic_capacity_minutes: 0,
      plannable_minutes: 0,
      allocated_minutes: 0,
      tasks: [],
      markers: d.markers ?? [],
      commitments: d.commitments ?? [],
    }));

  if (!sourceDays.length) {
    return <EmptyState title="Нет данных о нагрузке" />;
  }

  const visibleDays = windowRange === 'week1'
    ? sourceDays.slice(0, 7)
    : windowRange === 'week2'
      ? sourceDays.slice(7, 14)
      : sourceDays;

  const activeDay = selectedDate ? visibleDays.find((d) => d.date === selectedDate) ?? null : null;

  const totalBusy = visibleDays.reduce((sum, d) => sum + (d.busy_minutes || 0), 0);
  const totalStudy = visibleDays.reduce((sum, d) => sum + (d.allocated_minutes || 0), 0);
  const totalPlannable = visibleDays.reduce((sum, d) => sum + (d.plannable_minutes || 0), 0);
  const totalReserve = visibleDays.reduce((sum, d) => sum + Math.max(0, (d.realistic_capacity_minutes || 0) - (d.plannable_minutes || 0)), 0);
  const totalFreeSlack = Math.max(0, totalPlannable - totalStudy);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-3 text-[12px]">
          <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 inline-block" style={{ background: calendarColors.busy }} /> Дела: <strong className="tabular-nums">{durationLabel(totalBusy)}</strong></span>
          <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 inline-block" style={{ background: calendarColors.task }} /> Учёба: <strong className="tabular-nums">{durationLabel(totalStudy)}</strong></span>
          <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 inline-block" style={{ background: calendarColors.break }} /> Запас: <strong className="tabular-nums">{durationLabel(totalFreeSlack)}</strong></span>
          <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 inline-block bg-[#454545]" /> Резерв: <strong className="tabular-nums">{durationLabel(totalReserve)}</strong></span>
        </div>
        {sourceDays.length > 7 && (
          <div className="flex items-center gap-1">
            <Button size="xs" variant={windowRange === 'week1' ? 'primary' : 'default'} onClick={() => setWindowRange('week1')}>Дни 1–7</Button>
            <Button size="xs" variant={windowRange === 'week2' ? 'primary' : 'default'} onClick={() => setWindowRange('week2')}>Дни 8–14</Button>
            <Button size="xs" variant={windowRange === 'all' ? 'primary' : 'default'} onClick={() => setWindowRange('all')}>Все ({sourceDays.length})</Button>
          </div>
        )}
      </div>

      {/* Stacked load breakdown per day */}
      <div className="border border-[#303030] bg-[#111111] divide-y divide-[#1E1E1E]">
        {visibleDays.map((day) => {
          const wd = WEEKDAYS_RU[parseIsoDate(day.date).getUTCDay()];
          const busyM = day.busy_minutes || 0;
          const studyM = day.allocated_minutes || 0;
          const plannableM = day.plannable_minutes || 0;
          const realisticM = day.realistic_capacity_minutes || 0;
          const reserveM = Math.max(0, realisticM - plannableM);
          const freeStudyM = Math.max(0, plannableM - studyM);
          const restM = Math.max(0, 1440 - busyM - studyM - freeStudyM - reserveM);
          const load = LOAD_LABELS[day.load_level] || LOAD_LABELS.medium;
          const selected = activeDay?.date === day.date;

          return (
            <div
              key={day.date}
              className={`grid grid-cols-1 lg:grid-cols-[150px_minmax(0,1fr)_96px_170px_130px] items-center gap-2.5 px-3 py-2 cursor-pointer transition-colors ${selected ? 'bg-[#17141D]' : 'hover:bg-[#151515]'}`}
              onClick={() => setSelectedDate((prev) => (prev === day.date ? null : day.date))}
            >
              <div className="flex items-center justify-between lg:justify-start gap-2">
                <span className="text-[12px] font-semibold text-[#EAEAEA] tabular-nums">
                  {formatShortDate(day.date)} · {wd}
                </span>
                <Badge variant={load.variant}>{load.label}</Badge>
              </div>

              <div className="h-3.5 w-full bg-[#0A0A0A] border border-[#262626] flex overflow-hidden">
                {busyM > 0 && (
                  <HintTooltip label={`Постоянные дела: ${durationLabel(busyM)}`}>
                    <motion.div
                      initial={false}
                      animate={{ width: `${(busyM / 1440) * 100}%` }}
                      transition={LAYOUT_TRANSITION}
                      style={{ background: calendarColors.busy }}
                    />
                  </HintTooltip>
                )}
                {studyM > 0 && (
                  <HintTooltip label={`Учёба (Ameba): ${durationLabel(studyM)}`}>
                    <motion.div
                      initial={false}
                      animate={{ width: `${(studyM / 1440) * 100}%` }}
                      transition={LAYOUT_TRANSITION}
                      style={{ background: day.load_level === 'overload' ? '#c93b3b' : calendarColors.task }}
                    />
                  </HintTooltip>
                )}
                {freeStudyM > 0 && (
                  <HintTooltip label={`Запас ёмкости: ${durationLabel(freeStudyM)}`}>
                    <motion.div
                      initial={false}
                      animate={{ width: `${(freeStudyM / 1440) * 100}%` }}
                      transition={LAYOUT_TRANSITION}
                      style={{ background: calendarColors.break }}
                    />
                  </HintTooltip>
                )}
                {reserveM > 0 && (
                  <HintTooltip label={`Защищённый резерв: ${durationLabel(reserveM)}`}>
                    <motion.div
                      initial={false}
                      animate={{ width: `${(reserveM / 1440) * 100}%` }}
                      transition={LAYOUT_TRANSITION}
                      style={{ background: '#454545' }}
                    />
                  </HintTooltip>
                )}
                {restM > 0 && (
                  <HintTooltip label={`Сон и отдых: ${durationLabel(restM)}`}>
                    <motion.div
                      initial={false}
                      animate={{ width: `${(restM / 1440) * 100}%` }}
                      transition={LAYOUT_TRANSITION}
                      style={{ background: '#181818' }}
                    />
                  </HintTooltip>
                )}
              </div>

              <div className="text-[11px] text-[#B8B8B8] tabular-nums whitespace-nowrap">
                {durationLabel(studyM)} / {durationLabel(plannableM)}
              </div>

              <div className="text-[11px] text-[#929292] truncate">
                {day.tasks?.length ? day.tasks.map((t) => `${t.title} (${t.hours_label})`).join(', ') : '—'}
              </div>

              <div onClick={(event) => event.stopPropagation()}>
                {onAdjust && (
                  <SelectField
                    size="sm"
                    variant="ghost"
                    ariaLabel={`Режим дня ${day.date}`}
                    value={day.mode || 'auto'}
                    options={DAY_MODES}
                    disabled={busy}
                    onChange={(mode) => onAdjust({ day_overrides: { [day.date]: mode } })}
                  />
                )}
              </div>
            </div>
          );
        })}
      </div>

      <CollapseSection open={Boolean(activeDay)}>
        {activeDay && (
          <div className="border border-[#303030] bg-[#111111] p-3 flex flex-col gap-2 text-[12px]">
            <div className="flex items-center justify-between gap-2 border-b border-[#222222] pb-1.5">
              <span className="font-semibold text-[#EAEAEA]">
                {formatShortDate(activeDay.date)} · {WEEKDAYS_RU[parseIsoDate(activeDay.date).getUTCDay()]} · работа {durationLabel(activeDay.allocated_minutes)}
              </span>
              <IconButton size="icon-sm" variant="ghost" aria-label="Закрыть детали дня" onClick={() => setSelectedDate(null)}>
                <X className="h-3.5 w-3.5" />
              </IconButton>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div className="flex flex-col gap-1">
                {activeDay.tasks?.length ? (
                  activeDay.tasks.map((item) => (
                    <div key={item.task_id} className="flex items-center justify-between gap-2 border-l-2 border-[#7F49B4] pl-2 py-0.5">
                      <a href={`/?task=${item.task_id}`} className="truncate hover:underline">{item.title}</a>
                      <span className="text-[#B8B8B8] tabular-nums shrink-0">{item.hours_label}</span>
                    </div>
                  ))
                ) : (
                  <div className="text-[#929292]">Нет назначенных задач</div>
                )}
              </div>
              <div className="flex flex-col gap-1">
                <div className="text-[#929292]">
                  События ({activeDay.commitments?.length ?? 0}) · своб. {durationLabel(activeDay.physical_free_minutes)}
                </div>
                {activeDay.commitments?.map((c, idx) => (
                  <div key={`${c.id}-${idx}`} className="text-[#929292] tabular-nums">
                    {c.all_day ? 'Весь день' : `${c.start}–${c.end}`} · <span className="text-[#DCDCDC]">{c.title}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </CollapseSection>
    </div>
  );
}

const TIMELINE_START_MIN = 6 * 60;
const TIMELINE_END_MIN = 24 * 60;
const TIMELINE_SPAN = TIMELINE_END_MIN - TIMELINE_START_MIN;
const TIMELINE_PX_PER_MIN = 0.45;
const TIMELINE_HEIGHT = Math.round(TIMELINE_SPAN * TIMELINE_PX_PER_MIN);

function ExactTimelineTab({ baseDate, reasons = [], timeline = [], taskIds = [], manualOverrides = {}, onAdjust, busy }) {
  const defaultWeekStart = mondayIso(baseDate || new Date().toISOString().slice(0, 10));
  const [weekStart, setWeekStart] = useState(defaultWeekStart);
  const [estimatesOpen, setEstimatesOpen] = useState(false);
  const [editingEstimate, setEditingEstimate] = useState(null);
  const [estimateMinutes, setEstimateMinutes] = useState('');

  const weekEnd = addIsoDays(weekStart, 6);
  const calendar = useQuery({
    queryKey: ['calendar', weekStart, weekEnd],
    queryFn: () => request(`/calendar?start=${weekStart}&end=${weekEnd}`),
    refetchInterval: 10000,
  });

  const days = Array.from({ length: 7 }, (_, i) => addIsoDays(weekStart, i));
  const events = calendar.data?.events ?? [];
  const today = calendar.data?.today;

  const reasonsMap = new Map(reasons.map((r) => [r.task_id, r]));
  const timelineMap = new Map(timeline.map((t) => [t.id ?? t.task_id, t]));

  return (
    <div className="flex flex-col gap-3">
      {/* Compact Week Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="inline-flex items-center border border-[#303030] bg-[#111111]">
          <IconButton size="icon-sm" variant="ghost" aria-label="Предыдущая неделя" onClick={() => setWeekStart(addIsoDays(weekStart, -7))}>
            <ChevronLeft className="h-3.5 w-3.5" />
          </IconButton>
          <Button size="sm" variant="ghost" className="border-x border-[#262626]" onClick={() => setWeekStart(defaultWeekStart)}>
            {formatShortDate(weekStart)} – {formatShortDate(weekEnd)}
          </Button>
          <IconButton size="icon-sm" variant="ghost" aria-label="Следующая неделя" onClick={() => setWeekStart(addIsoDays(weekStart, 7))}>
            <ChevronRight className="h-3.5 w-3.5" />
          </IconButton>
        </div>

        <div className="flex items-center gap-3 text-[11px] text-[#929292]">
          <span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 inline-block" style={{ background: calendarColors.busy }} /> Календарь</span>
          <span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 inline-block border border-dashed border-[#B98CE8]" style={{ background: calendarBlockColor('task', true) }} /> Блок Ameba</span>
        </div>
      </div>

      {calendar.isPending ? (
        <PageLoader label="Загрузка шкалы…" />
      ) : calendar.error ? (
        <Notice tone="error">{calendar.error.message}</Notice>
      ) : (
        <div className="border border-[#303030] bg-[#0D0D0D] overflow-x-auto">
          <div className="min-w-[780px]">
            {/* Day headers */}
            <div className="grid grid-cols-[44px_repeat(7,1fr)] bg-[#111111] border-b border-[#303030]">
              <div />
              {days.map((day) => {
                const wd = WEEKDAYS_RU[parseIsoDate(day).getUTCDay()];
                const isToday = day === today;
                return (
                  <div
                    key={day}
                    className={`py-1.5 px-1 text-center text-[11px] border-l border-[#242424] tabular-nums ${isToday ? 'bg-[#7F49B4]/16 text-[#DFC4F8] font-semibold' : 'text-[#EAEAEA]'}`}
                  >
                    {formatShortDate(day)} · {wd}
                  </div>
                );
              })}
            </div>

            {/* Hourly grid (06:00–24:00) */}
            <div className="grid grid-cols-[44px_repeat(7,1fr)]">
              <div className="relative bg-[#0D0D0D] text-[10px] text-[#7D7D7D] tabular-nums" style={{ height: TIMELINE_HEIGHT }}>
                {[6, 8, 10, 12, 14, 16, 18, 20, 22].map((hour) => (
                  <span
                    key={hour}
                    className="absolute right-1.5 -translate-y-1.5"
                    style={{ top: (hour * 60 - TIMELINE_START_MIN) * TIMELINE_PX_PER_MIN }}
                  >
                    {String(hour).padStart(2, '0')}
                  </span>
                ))}
              </div>

              {days.map((day) => {
                const dayEvents = events.filter((ev) => ev.date === day);
                const workWindows = dayEvents.filter((ev) => ev.kind === 'work');
                const blocks = layoutOverlaps(dayEvents.filter((ev) => ev.kind !== 'work'));

                return (
                  <div
                    key={day}
                    className="relative border-l border-[#222222]"
                    style={{ height: TIMELINE_HEIGHT }}
                  >
                    {[8, 10, 12, 14, 16, 18, 20, 22].map((hour) => (
                      <div
                        key={hour}
                        className="absolute left-0 right-0 h-px bg-[#181818]"
                        style={{ top: (hour * 60 - TIMELINE_START_MIN) * TIMELINE_PX_PER_MIN }}
                      />
                    ))}

                    {workWindows.map((ev, idx) => {
                      const s = Math.max(TIMELINE_START_MIN, toMin(ev.start));
                      const e = Math.min(TIMELINE_END_MIN, toMin(ev.end));
                      if (e <= s) return null;
                      return (
                        <div
                          key={`w-${ev.id}-${idx}`}
                          className="absolute left-0 right-0 border-l-2 border-[#527C42] bg-[#527C42]/12 pointer-events-none"
                          style={{
                            top: (s - TIMELINE_START_MIN) * TIMELINE_PX_PER_MIN,
                            height: (e - s) * TIMELINE_PX_PER_MIN,
                          }}
                        />
                      );
                    })}

                    {blocks.map((ev, idx) => {
                      const s = Math.max(TIMELINE_START_MIN, toMin(ev.start));
                      const e = Math.min(TIMELINE_END_MIN, toMin(ev.end));
                      if (e <= s) return null;
                      const isTemp = ev.source === 'ameba';
                      const top = (s - TIMELINE_START_MIN) * TIMELINE_PX_PER_MIN;
                      const height = Math.max(12, (e - s) * TIMELINE_PX_PER_MIN);
                      const style = {
                        top,
                        height,
                        left: `calc(${(ev.lane / ev.columns) * 100}% + 1px)`,
                        width: `calc(${100 / ev.columns}% - 2px)`,
                        background: calendarBlockColor(ev.kind, isTemp),
                        borderColor: isTemp ? '#B98CE8' : calendarColors[ev.kind],
                      };

                      const inner = (
                        <div
                          className={`absolute px-1 text-[10px] leading-tight overflow-hidden text-[#EAEAEA] ${isTemp ? 'border border-dashed' : ''}`}
                          style={style}
                        >
                          <div className="truncate font-medium">{ev.start} {ev.title}</div>
                        </div>
                      );

                      return (
                        <HintTooltip key={`${ev.id}-${idx}`} label={`${ev.start}–${ev.end} · ${ev.title}${isTemp ? ' (Ameba)' : ''}`}>
                          {ev.task_id ? (
                            <a href={`/?task=${ev.task_id}`} className="block">{inner}</a>
                          ) : inner}
                        </HintTooltip>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* Optional compact estimates table behind toggle */}
      {taskIds.length > 0 && (
        <div className="flex flex-col gap-2">
          <button
            type="button"
            onClick={() => setEstimatesOpen(!estimatesOpen)}
            aria-expanded={estimatesOpen}
            className="inline-flex items-center gap-1.5 text-[12px] text-[#929292] hover:text-[#EAEAEA] w-fit cursor-pointer"
          >
            {estimatesOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
            <span>Оценки задач ({taskIds.length})</span>
          </button>

          <CollapseSection open={estimatesOpen}>
            <TableContainer>
              <Table className="min-w-[680px]">
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-10">#</TableHead>
                    <TableHead>Задача</TableHead>
                    <TableHead className="w-24">Оценка</TableHead>
                    <TableHead>Причина</TableHead>
                    <TableHead className="w-36 text-right" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {taskIds.map((tid, idx) => {
                    const reason = reasonsMap.get(tid);
                    const tItem = timelineMap.get(tid);
                    const title = tItem?.title ?? `Задача #${tid}`;
                    const plan = reason?.plan ?? tItem?.plan;
                    const manualVal = manualOverrides[String(tid)];
                    const editingThis = editingEstimate === tid;

                    return (
                      <tr key={tid} className={tableRowClass}>
                        <TableCell className="tabular-nums font-semibold text-[#B98CE8]">{idx + 1}</TableCell>
                        <TableCell>
                          <div className="flex items-center gap-1.5 min-w-0">
                            <a href={`/?task=${tid}`} className="font-medium text-[#EAEAEA] hover:text-[#CFA9F5] hover:underline truncate">
                              {title}
                            </a>
                            <DeadlineRisk reason={reason?.deadline_risk} />
                            {manualVal !== undefined && <Badge variant="info">Ручн. {manualVal}м</Badge>}
                          </div>
                        </TableCell>
                        <TableCell className="tabular-nums text-[12px]">
                          {plan?.hours_label ?? reason?.time_estimate ?? '—'}
                        </TableCell>
                        <TableCell className="text-[12px] text-[#929292] max-w-[300px]">
                          {reason?.reason ? (
                            <HintTooltip label={reason.reason}>
                              <span className="line-clamp-1 cursor-help">{reason.reason}</span>
                            </HintTooltip>
                          ) : '—'}
                        </TableCell>
                        <TableCell className="text-right">
                          {editingThis ? (
                            <div className="inline-flex items-center gap-1 justify-end">
                              <Input
                                type="number"
                                min={0}
                                max={10000}
                                placeholder="Мин"
                                value={estimateMinutes}
                                onChange={(event) => setEstimateMinutes(event.currentTarget.value)}
                                className="w-[70px] h-[24px] text-[12px]"
                              />
                              <Button
                                size="xs"
                                variant="primary"
                                disabled={busy}
                                onClick={() => {
                                  const val = estimateMinutes.trim() === '' ? null : Math.max(0, Number(estimateMinutes));
                                  onAdjust?.({ estimate_override: { task_id: tid, remaining_minutes: val } });
                                  setEditingEstimate(null);
                                }}
                              >
                                OK
                              </Button>
                              <Button size="xs" variant="ghost" onClick={() => setEditingEstimate(null)}>✕</Button>
                            </div>
                          ) : (
                            <div className="inline-flex items-center gap-1 justify-end">
                              <Button
                                size="xs"
                                variant="subtle"
                                disabled={busy}
                                onClick={() => {
                                  setEditingEstimate(tid);
                                  setEstimateMinutes(manualVal !== undefined ? String(manualVal) : String(plan?.work_max_minutes ?? ''));
                                }}
                              >
                                Остаток
                              </Button>
                              {manualVal !== undefined && (
                                <Button
                                  size="xs"
                                  variant="ghost"
                                  disabled={busy}
                                  onClick={() => onAdjust?.({ estimate_override: { task_id: tid, remaining_minutes: null } })}
                                >
                                  Сброс
                                </Button>
                              )}
                            </div>
                          )}
                        </TableCell>
                      </tr>
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          </CollapseSection>
        </div>
      )}
    </div>
  );
}

export default function AmebaWorkspace({ features = {}, embedded = false }) {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState('periods');
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [assumptionsOpen, setAssumptionsOpen] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const [planFeedback, setPlanFeedback] = useState(null);

  const ranking = useQuery({
    queryKey: ['ameba-ranking'],
    queryFn: () => request('/integrations/ameba/ranking'),
    enabled: features.ameba !== false,
    refetchInterval: 15000,
  });
  const queue = useQuery({
    queryKey: ['agent-queue'],
    queryFn: () => request('/integrations/queue'),
    enabled: features.monocle !== false || features.ameba !== false,
    refetchInterval: 1000,
  });
  const livePlan = useQuery({
    queryKey: ['ameba-live-plan'],
    queryFn: async () => null,
    enabled: false,
    initialData: null,
  });

  const refresh = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['tasks'] }),
    queryClient.invalidateQueries({ queryKey: ['ameba-ranking'] }),
  ]);

  const rank = useMutation({
    mutationFn: () => agentRequest('/integrations/ameba/rank', {
      method: 'POST',
      body: JSON.stringify({ feedback: planFeedback ?? ranking.data?.feedback ?? '' }),
    }),
    onSuccess: async () => {
      await refresh();
    },
  });

  const clearPlan = useMutation({
    mutationFn: () => request('/integrations/ameba/plan', { method: 'DELETE' }),
    onSuccess: async () => {
      rank.reset();
      queryClient.setQueryData(['ameba-live-plan'], null);
      await Promise.all([refresh(), queryClient.invalidateQueries({ queryKey: ['calendar'] })]);
    },
  });

  const feedback = useMutation({
    mutationFn: (payload) => request('/integrations/ameba/feedback', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
    onSuccess: refresh,
  });

  const adjustPlan = useMutation({
    mutationFn: (payload) => request('/integrations/ameba/plan', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
    onSuccess: refresh,
  });

  const data = ranking.data;
  const liveActive = Boolean(livePlan.data?.running || (data?.has_plan && livePlan.data?.started_at && livePlan.data.started_at > (data?.generated_at ?? '')));
  const activeSchedule = (liveActive ? livePlan.data?.schedule : null) ?? (data?.plan_reset ? null : data?.schedule);
  const activeTimeline = (liveActive ? livePlan.data?.timeline : null) ?? data?.timeline ?? [];
  const manual = data?.manual_adjustments ?? {};
  const hasManualAdjustments = Boolean(
    Object.keys(manual.day_overrides ?? {}).length
    || Object.keys(manual.task_pins ?? {}).length
    || Object.keys(manual.estimate_overrides ?? {}).length,
  );
  const isRunning = Boolean(queue.data?.ameba_running || rank.isPending);
  const status = resolvePlanStatus({
    rankingData: data,
    liveActive,
    isRunning,
    rankError: rank.error,
    failedRun: ranking.error,
  });

  const unfitted = (activeSchedule?.remaining ?? []).filter((r) => r.remaining_work_minutes > 0);
  const titleMap = new Map(activeTimeline.map((item) => [item.id ?? item.task_id, item.title]));
  const conflicts = activeSchedule?.deadline_conflicts ?? [];
  const hasSidebar = unfitted.length > 0 || conflicts.length > 0;
  const error = ranking.error || rank.error || clearPlan.error || feedback.error || adjustPlan.error;
  const reservePct = activeSchedule?.reserve_ratio != null ? Math.round(activeSchedule.reserve_ratio * 100) : 22;
  const actionSize = embedded ? 'sm' : 'default';

  const actionsBar = (
    <>
      <Button
        size={actionSize}
        variant={feedbackOpen ? 'accent-subtle' : 'outline'}
        onClick={() => setFeedbackOpen(!feedbackOpen)}
        rightIcon={feedbackOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
      >
        Пожелания
      </Button>
      <Button
        size={actionSize}
        variant="primary"
        leftIcon={<RefreshCw className={`h-3.5 w-3.5 ${isRunning ? 'animate-spin' : ''}`} />}
        loading={rank.isPending && !queue.data?.ameba_running}
        disabled={clearPlan.isPending || queue.data?.browser_busy}
        onClick={() => rank.mutate()}
      >
        {queue.data?.ameba_running ? 'Применить' : 'Обновить план'}
      </Button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <IconButton size={embedded ? 'icon-sm' : 'icon'} variant="default" aria-label="Дополнительные действия">
            <MoreHorizontal className="h-3.5 w-3.5" />
          </IconButton>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          {data?.explanation && (
            <DropdownMenuItem onClick={() => setSummaryOpen(!summaryOpen)}>
              {summaryOpen ? 'Скрыть сводку плана' : 'Сводка плана'}
            </DropdownMenuItem>
          )}
          <DropdownMenuItem onClick={() => setAssumptionsOpen(!assumptionsOpen)}>
            {assumptionsOpen ? 'Скрыть допущения' : 'Допущения планировщика'}
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => setLogOpen(!logOpen)}>
            <Terminal className="h-3.5 w-3.5" />
            {logOpen ? 'Скрыть журнал Ameba' : 'Журнал Ameba'}
          </DropdownMenuItem>
          {hasManualAdjustments && (
            <DropdownMenuItem disabled={isRunning} onClick={() => adjustPlan.mutate({ reset: true })}>
              <RotateCcw className="h-3.5 w-3.5" />
              Сбросить правки
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            danger
            disabled={!data?.has_plan || isRunning || clearPlan.isPending}
            onClick={() => {
              if (window.confirm('Удалить сохранённый план Ameba и ручные корректировки?')) clearPlan.mutate();
            }}
          >
            <Trash2 className="h-3.5 w-3.5" />
            Удалить план
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  );

  return (
    <div className="flex flex-col gap-3">
      {!embedded ? (
        <PageHeader
          title="План · Ameba"
          badge={<Badge variant={status.badge}>{status.label}</Badge>}
          actions={actionsBar}
        />
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-2 border border-[#303030] bg-[#111111] px-3 py-2">
          <div className="flex items-center gap-2">
            <StatusIndicator tone={status.tone} pulse={status.pulse} />
            <span className="text-[13px] font-semibold text-[#EAEAEA]">Ameba</span>
            <Badge variant={status.badge}>{status.label}</Badge>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">{actionsBar}</div>
        </div>
      )}

      {data?.explanation && (
        <CollapseSection open={summaryOpen}>
          <div className="border border-[#303030] bg-[#111111] px-3 py-2 text-[12px] text-[#D0D0D0] whitespace-pre-wrap break-words flex items-start justify-between gap-2">
            <span>{data.explanation}</span>
            <IconButton size="icon-sm" variant="ghost" aria-label="Закрыть сводку" onClick={() => setSummaryOpen(false)}>
              <X className="h-3.5 w-3.5" />
            </IconButton>
          </div>
        </CollapseSection>
      )}

      <CollapseSection open={assumptionsOpen}>
        <div className="border border-[#303030] bg-[#111111] px-3 py-2 flex flex-col gap-1 text-[12px]">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[#EAEAEA]">
              {activeSchedule?.rest_assumption || data?.calendar_schedule?.rest_assumption || 'Сон ~8 ч, базовый быт и перерывы защищены.'}
            </span>
            <IconButton size="icon-sm" variant="ghost" aria-label="Закрыть допущения" onClick={() => setAssumptionsOpen(false)}>
              <X className="h-3.5 w-3.5" />
            </IconButton>
          </div>
          {activeSchedule?.adaptation_summary && (
            <div className="text-[#929292]">{activeSchedule.adaptation_summary}</div>
          )}
          {data?.observations_count > 0 && (
            <div className="text-[11px] text-[#7D7D7D] tabular-nums">Откликов: {data.observations_count}</div>
          )}
        </div>
      </CollapseSection>

      <CollapseSection open={feedbackOpen}>
        <div className="border border-[#303030] bg-[#111111] p-3 flex flex-col gap-2">
          <div className="text-[12px] font-medium text-[#EAEAEA]">Пожелания к нагрузке и отдыху</div>
          <Textarea
            rows={2}
            maxLength={4000}
            placeholder="Например: пятницу сделай облегчённой, сложные задачи ставь до 18:00"
            value={planFeedback ?? data?.feedback ?? ''}
            onChange={(event) => setPlanFeedback(event.currentTarget.value)}
          />
          <div className="flex justify-end">
            <Button
              size="sm"
              variant="primary"
              disabled={clearPlan.isPending || queue.data?.browser_busy}
              onClick={() => rank.mutate()}
            >
              Применить и пересчитать
            </Button>
          </div>
        </div>
      </CollapseSection>

      {rank.data?.unchanged && (
        <Notice tone="info">
          {rank.data.status === 'running' ? 'Ameba уже работает с этими данными.' : 'Данные не изменились.'}
        </Notice>
      )}
      {error && <Notice tone="error">{error.message}</Notice>}

      {/* Tabs + Inline Metrics Row + Conditional Risks Sidebar */}
      <div className={hasSidebar ? 'grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_300px] gap-4 items-start' : 'grid grid-cols-1'}>
        <Tabs value={tab} onValueChange={setTab} className="min-w-0">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#303030]">
            <TabsList className="border-b-0">
              <TabsTrigger value="periods">Периоды</TabsTrigger>
              <TabsTrigger value="load">Нагрузка</TabsTrigger>
              <TabsTrigger value="timeline">Временная шкала</TabsTrigger>
            </TabsList>

            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 pb-1.5 text-[12px] text-[#929292] tabular-nums">
              {activeSchedule?.hours_label && (
                <span>Объём: <strong className="text-[#EAEAEA] font-medium">{activeSchedule.hours_label}</strong></span>
              )}
              <span>
                Задач: <strong className="text-[#EAEAEA] font-medium">{data?.ranked_count ?? 0}</strong>
                {data?.pending_count > 0 && <span className="text-[#D4A24C]"> (+{data.pending_count})</span>}
              </span>
              <span>Резерв: <strong className="text-[#EAEAEA] font-medium">{reservePct}%</strong></span>
              {(unfitted.length > 0 || conflicts.length > 0) && (
                <span className="text-[#D4A24C]">
                  {unfitted.length ? `Не помест.: ${unfitted.length}` : `Конфл.: ${conflicts.length}`}
                </span>
              )}
            </div>
          </div>

          <TabsContent value="periods">
            <TabTransition transitionKey="periods">
              <AgentTimeline
                tasks={activeTimeline}
                schedule={activeSchedule ?? data?.calendar_schedule}
                calendarOnly={!activeSchedule}
                finished={!livePlan.data?.running && !queue.data?.ameba_running}
                busy={feedback.isPending || adjustPlan.isPending || rank.isPending}
                onFeedback={(payload) => feedback.mutate(payload)}
                onAdjust={(payload) => adjustPlan.mutate(payload)}
                taskPins={manual.task_pins ?? {}}
                estimateOverrides={manual.estimate_overrides ?? {}}
                onRefresh={() => rank.mutate()}
                hideConflicts={hasSidebar}
              />
            </TabTransition>
          </TabsContent>

          <TabsContent value="load">
            <TabTransition transitionKey="load">
              <LoadAnalysisTab
                daily={activeSchedule?.daily_allocations ?? []}
                previewDays={data?.calendar_schedule?.days_summary ?? []}
                onAdjust={(payload) => adjustPlan.mutate(payload)}
                busy={adjustPlan.isPending || isRunning}
              />
            </TabTransition>
          </TabsContent>

          <TabsContent value="timeline">
            <TabTransition transitionKey="timeline">
              <ExactTimelineTab
                baseDate={activeSchedule?.generated_date}
                reasons={data?.reasons ?? []}
                timeline={activeTimeline}
                taskIds={data?.task_ids ?? []}
                manualOverrides={manual.estimate_overrides ?? {}}
                onAdjust={(payload) => adjustPlan.mutate(payload)}
                busy={adjustPlan.isPending || isRunning}
              />
            </TabTransition>
          </TabsContent>
        </Tabs>

        {hasSidebar && (
          <aside className="flex flex-col gap-3">
            {unfitted.length > 0 && (
              <div className="border border-[#303030] bg-[#111111] p-3 flex flex-col gap-2">
                <SectionHeader title="Не поместилось" meta={`${unfitted.length}`} />
                <div className="flex flex-col divide-y divide-[#1E1E1E]">
                  {unfitted.map((item) => (
                    <div key={item.task_id} className="py-1.5 flex items-center justify-between gap-2 text-[12px]">
                      <a href={`/?task=${item.task_id}`} className="truncate hover:underline text-[#EAEAEA]">
                        {titleMap.get(item.task_id) ?? `Задача #${item.task_id}`}
                      </a>
                      <Badge variant="warning" className="tabular-nums shrink-0">
                        {durationLabel(item.remaining_work_minutes)}
                      </Badge>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {conflicts.length > 0 && (
              <div className="border border-[#303030] bg-[#111111] p-3 flex flex-col gap-2">
                <SectionHeader title="Риски дедлайнов" meta={`${conflicts.length}`} />
                <div className="flex flex-col gap-2">
                  {conflicts.map((c) => (
                    <div key={c.task_id} className="border-l-2 border-[#D4A24C] pl-2 text-[12px]">
                      <div className="font-medium text-[#EAEAEA]">{c.reason}</div>
                      {c.options?.length > 0 && (
                        <div className="text-[11px] text-[#929292] mt-0.5">{c.options.join(' · ')}</div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </aside>
        )}
      </div>

      <AgentProgress
        providers={['ameba']}
        active={rank.isPending}
        defaultOpen={false}
        hideSummaryWhenIdle
        open={logOpen}
        onOpenChange={setLogOpen}
      />
    </div>
  );
}
