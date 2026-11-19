import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowRight,
  Brain,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  MoreHorizontal,
  RefreshCw,
  RotateCcw,
  Terminal,
  Trash2,
  X,
} from 'lucide-react';
import { agentRequest, request } from './api';
import AgentMemory from './AgentMemory';
import AgentTimeline, { durationLabel, formatShortDate, parseIsoDate } from './AgentTimeline';
import AgentProgress from './AgentProgress';
import { DeadlineRisk } from './RankingAssessment';
import { calendarColors } from './calendarColors';
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
import { Badge, EmptyState, Notice, PageHeader, SectionHeader, StatusIndicator } from '@/components/ui/surface';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { HintTooltip } from '@/components/ui/tooltip';
import { CollapseSection, LAYOUT_TRANSITION, LiveHorizon, motion, TabTransition, useReducedMotion } from '@/lib/motion';

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
  const horizonTone = status.badge === 'warning' ? 'warning' : status.badge === 'danger' ? 'danger' : 'accent';
  const actionLabel = isRunning ? 'Считает…' : !data?.has_plan ? 'Построить план' : 'Пересчитать';
  const actionVariant = !data?.has_plan || data?.needs_refresh ? 'primary' : 'default';

  return (
    <div className="relative overflow-hidden border border-[#303030] bg-[#111111] px-3 py-2 flex flex-wrap items-center justify-between gap-2">
      <LiveHorizon active={isRunning || liveActive} tone={horizonTone} />
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
          <HintTooltip label="Задачи, для которых Monocle ещё собирает или уточняет контекст">
            <span className="text-[#D4A24C] tabular-nums cursor-help">Ждут Monocle: {data.pending_count}</span>
          </HintTooltip>
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
          <span className="text-[#B8B8B8] truncate max-w-[260px]">{focusPeriod.title}</span>
        ) : data?.explanation ? (
          <span className="text-[#929292] truncate max-w-[260px]">{data.explanation}</span>
        ) : topTaskReason ? (
          <span className="text-[#929292] truncate max-w-[200px]">{topTaskReason.time_estimate}</span>
        ) : null}
      </div>

      <div className="flex items-center gap-1.5 shrink-0 max-sm:w-full">
        {rank?.data?.unchanged && (
          <span className="text-[11px] text-[#929292] hidden xl:inline">План актуален</span>
        )}
        <Button
          size="sm"
          variant={actionVariant}
          leftIcon={<RefreshCw className={`h-3 w-3 ${isRunning ? 'animate-spin' : ''}`} />}
          loading={rank?.isPending && !queue?.data?.ameba_running}
          disabled={isRunning || queue?.data?.browser_busy}
          onClick={onRefresh}
          className="max-sm:flex-1"
        >
          {actionLabel}
        </Button>
        <Button size="sm" variant="accent-subtle" asChild className="max-sm:flex-1">
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
  const reduced = useReducedMotion();
  const barTransition = reduced ? { duration: 0.01 } : LAYOUT_TRANSITION;
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
    return (
      <EmptyState
        title="Нет данных о нагрузке"
        description="Постройте план или добавьте события в постоянный календарь, чтобы увидеть доступную ёмкость по дням."
        action={(
          <Button size="sm" variant="outline" asChild>
            <a href="/calendar">Открыть календарь</a>
          </Button>
        )}
      />
    );
  }

  const visibleDays = windowRange === 'week1'
    ? sourceDays.slice(0, 7)
    : windowRange === 'week2'
      ? sourceDays.slice(7, 14)
      : sourceDays;

  const totalBusy = visibleDays.reduce((sum, d) => sum + (d.busy_minutes || 0), 0);
  const totalStudy = visibleDays.reduce((sum, d) => sum + (d.allocated_minutes || 0), 0);
  const totalPlannable = visibleDays.reduce((sum, d) => sum + (d.plannable_minutes || 0), 0);
  const totalReserve = visibleDays.reduce((sum, d) => sum + Math.max(0, (d.realistic_capacity_minutes || 0) - (d.plannable_minutes || 0)), 0);
  const totalFreeSlack = Math.max(0, totalPlannable - totalStudy);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px]">
          <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 inline-block" style={{ background: calendarColors.busy }} /> Занято в календаре: <strong className="tabular-nums">{durationLabel(totalBusy)}</strong></span>
          <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 inline-block" style={{ background: calendarColors.task }} /> Учёба по плану: <strong className="tabular-nums">{durationLabel(totalStudy)}</strong></span>
          <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 inline-block" style={{ background: calendarColors.break }} /> Свободный запас: <strong className="tabular-nums">{durationLabel(totalFreeSlack)}</strong></span>
          <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 inline-block bg-[#454545]" /> Защищённый резерв: <strong className="tabular-nums">{durationLabel(totalReserve)}</strong></span>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {sourceDays.length > 7 && (
            <div className="flex items-center gap-1">
              <Button size="xs" variant={windowRange === 'week1' ? 'primary' : 'default'} onClick={() => setWindowRange('week1')}>Дни 1–7</Button>
              <Button size="xs" variant={windowRange === 'week2' ? 'primary' : 'default'} onClick={() => setWindowRange('week2')}>Дни 8–14</Button>
              <Button size="xs" variant={windowRange === 'all' ? 'primary' : 'default'} onClick={() => setWindowRange('all')}>Все ({sourceDays.length})</Button>
            </div>
          )}
          <a href="/calendar" className="inline-flex items-center gap-1 text-[12px] text-[#CFA9F5] hover:underline">
            Открыть календарь
            <ExternalLink className="h-3 w-3" />
          </a>
        </div>
      </div>

      {/* Stacked load breakdown per day */}
      <div className="border border-[#303030] bg-[#111111] divide-y divide-[#1E1E1E]">
        <div className="hidden lg:grid lg:grid-cols-[155px_minmax(0,1fr)_120px_170px_135px] gap-2.5 px-3 py-1.5 bg-[#0D0D0D] text-[11px] font-medium text-[#7D7D7D]">
          <span>День</span>
          <span>Распределение суток</span>
          <span>План / доступно</span>
          <span>Задачи</span>
          <span>Режим дня</span>
        </div>
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
          const selected = selectedDate === day.date;

          return (
            <div key={day.date} className={selected ? 'bg-[#17141D]' : ''}>
              <div
                className={`grid grid-cols-1 lg:grid-cols-[155px_minmax(0,1fr)_120px_170px_135px] items-center gap-2.5 px-3 py-2 cursor-pointer transition-colors ${selected ? '' : 'hover:bg-[#151515]'}`}
                onClick={() => setSelectedDate((prev) => (prev === day.date ? null : day.date))}
              >
                <div className="flex items-center justify-between lg:justify-start gap-2">
                  <span className="text-[12px] font-semibold text-[#EAEAEA] tabular-nums">
                    {formatShortDate(day.date)} · {wd}
                  </span>
                  <Badge variant={load.variant}>{load.label}</Badge>
                </div>

                <div className="h-3.5 max-md:h-4 w-full bg-[#0A0A0A] border border-[#262626] flex overflow-hidden">
                  {busyM > 0 && (
                    <HintTooltip label={`Постоянные дела: ${durationLabel(busyM)}`}>
                      <motion.div
                        initial={false}
                        animate={{ width: `${(busyM / 1440) * 100}%` }}
                        transition={barTransition}
                        style={{ background: calendarColors.busy }}
                      />
                    </HintTooltip>
                  )}
                  {studyM > 0 && (
                    <HintTooltip label={`Учёба (Ameba): ${durationLabel(studyM)}`}>
                      <motion.div
                        initial={false}
                        animate={{ width: `${(studyM / 1440) * 100}%` }}
                        transition={barTransition}
                        style={{ background: day.load_level === 'overload' ? '#c93b3b' : calendarColors.task }}
                      />
                    </HintTooltip>
                  )}
                  {freeStudyM > 0 && (
                    <HintTooltip label={`Свободный запас: ${durationLabel(freeStudyM)}`}>
                      <motion.div
                        initial={false}
                        animate={{ width: `${(freeStudyM / 1440) * 100}%` }}
                        transition={barTransition}
                        style={{ background: calendarColors.break }}
                      />
                    </HintTooltip>
                  )}
                  {reserveM > 0 && (
                    <HintTooltip label={`Защищённый резерв: ${durationLabel(reserveM)}`}>
                      <motion.div
                        initial={false}
                        animate={{ width: `${(reserveM / 1440) * 100}%` }}
                        transition={barTransition}
                        style={{ background: '#454545' }}
                      />
                    </HintTooltip>
                  )}
                  {restM > 0 && (
                    <HintTooltip label={`Сон и отдых: ${durationLabel(restM)}`}>
                      <motion.div
                        initial={false}
                        animate={{ width: `${(restM / 1440) * 100}%` }}
                        transition={barTransition}
                        style={{ background: '#181818' }}
                      />
                    </HintTooltip>
                  )}
                </div>

                <div className="flex items-center justify-between gap-2 lg:contents">
                  <div className="text-[11px] text-[#B8B8B8] tabular-nums whitespace-nowrap">
                    <span className="lg:hidden text-[#7D7D7D]">План / доступно: </span>
                    {durationLabel(studyM)} / {durationLabel(plannableM)}
                  </div>

                  <div className="hidden lg:block text-[11px] text-[#929292] truncate">
                    {day.tasks?.length ? day.tasks.map((t) => `${t.title} (${t.hours_label})`).join(', ') : '—'}
                  </div>

                  <div onClick={(event) => event.stopPropagation()}>
                    {onAdjust && (
                      <SelectField
                        size="sm"
                        variant="ghost"
                        className="max-lg:border-[#303030] max-lg:bg-[#0D0D0D] max-lg:w-[140px]"
                        ariaLabel={`Режим дня ${day.date}`}
                        value={day.mode || 'auto'}
                        options={DAY_MODES}
                        disabled={busy}
                        onChange={(mode) => onAdjust({ day_overrides: { [day.date]: mode } })}
                      />
                    )}
                  </div>
                </div>

                {day.tasks?.length > 0 && (
                  <div className="lg:hidden text-[11px] text-[#929292] truncate">
                    {day.tasks.map((t) => `${t.title} (${t.hours_label})`).join(', ')}
                  </div>
                )}
              </div>

              <CollapseSection open={selected}>
                {selected && (
                  <div className="border-t border-[#222222] bg-[#0D0D0D] px-3 py-2.5 flex flex-col gap-2 text-[12px]">
                    <div className="flex items-center justify-between gap-2 border-b border-[#1E1E1E] pb-1.5">
                      <span className="font-semibold text-[#EAEAEA]">
                        {formatShortDate(day.date)} · {wd} · работа {durationLabel(day.allocated_minutes)}
                      </span>
                      <IconButton size="icon-sm" variant="ghost" aria-label="Закрыть детали дня" onClick={() => setSelectedDate(null)}>
                        <X className="h-3.5 w-3.5" />
                      </IconButton>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <div className="flex flex-col gap-1">
                        {day.tasks?.length ? (
                          day.tasks.map((item) => (
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
                          События ({day.commitments?.length ?? 0}) · своб. {durationLabel(day.physical_free_minutes)}
                        </div>
                        {day.commitments?.map((c, idx) => (
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
        })}
      </div>
    </div>
  );
}

function PriorityTasksTab({ reasons = [], timeline = [], taskIds = [], manualOverrides = {}, onAdjust, busy }) {
  const [editingEstimate, setEditingEstimate] = useState(null);
  const [estimateMinutes, setEstimateMinutes] = useState('');
  const [openSteps, setOpenSteps] = useState({});

  if (!taskIds.length) {
    return (
      <EmptyState
        title="Нет оценённых задач"
        description="Когда Ameba построит план, здесь появится приоритетный порядок задач, оценка времени и обоснование."
      />
    );
  }

  const reasonsMap = new Map(reasons.map((r) => [r.task_id, r]));
  const timelineMap = new Map(timeline.map((t) => [t.id ?? t.task_id, t]));

  return (
    <div className="border border-[#303030] bg-[#111111] divide-y divide-[#222222]">
      {taskIds.map((tid, idx) => {
        const reason = reasonsMap.get(tid);
        const tItem = timelineMap.get(tid);
        const title = tItem?.title ?? `Задача #${tid}`;
        const plan = reason?.plan ?? tItem?.plan;
        const manualVal = manualOverrides[String(tid)];
        const editingThis = editingEstimate === tid;
        const stepsOpen = Boolean(openSteps[tid]);
        const steps = plan?.steps ?? [];
        const assumptions = plan?.assumptions ?? [];

        return (
          <div key={tid} className="px-3.5 py-3 flex flex-col gap-2">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2 min-w-0">
                <span className="text-[13px] font-semibold text-[#B98CE8] tabular-nums">#{idx + 1}</span>
                <a href={`/?task=${tid}`} className="text-[14px] font-medium text-[#EAEAEA] hover:text-[#CFA9F5] hover:underline break-words">
                  {title}
                </a>
                <DeadlineRisk reason={reason?.deadline_risk} />
                {(plan?.hours_label || reason?.time_estimate) && (
                  <Badge variant="accent" className="tabular-nums">{plan?.hours_label ?? reason?.time_estimate}</Badge>
                )}
                {plan?.completed_work_minutes > 0 && (
                  <Badge variant="outline" className="tabular-nums">Сделано {plan.completed_work_minutes} мин</Badge>
                )}
                {manualVal !== undefined && (
                  <Badge variant="info" className="tabular-nums">Ручной остаток {manualVal} мин</Badge>
                )}
              </div>

              <div className="shrink-0">
                {editingThis ? (
                  <div className="inline-flex flex-wrap items-center gap-1">
                    <Input
                      type="number"
                      min={0}
                      max={10000}
                      placeholder="Минуты"
                      value={estimateMinutes}
                      onChange={(event) => setEstimateMinutes(event.currentTarget.value)}
                      className="w-[88px] max-md:w-[100px] h-[26px] text-[12px]"
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
                      Сохранить
                    </Button>
                    <Button size="xs" variant="ghost" onClick={() => setEditingEstimate(null)}>✕</Button>
                  </div>
                ) : (
                  <div className="inline-flex items-center gap-1">
                    <Button
                      size="xs"
                      variant="subtle"
                      disabled={busy}
                      onClick={() => {
                        setEditingEstimate(tid);
                        setEstimateMinutes(manualVal !== undefined ? String(manualVal) : String(plan?.work_max_minutes ?? ''));
                      }}
                    >
                      Изменить остаток
                    </Button>
                    {manualVal !== undefined && (
                      <Button
                        size="xs"
                        variant="ghost"
                        disabled={busy}
                        onClick={() => onAdjust?.({ estimate_override: { task_id: tid, remaining_minutes: null } })}
                      >
                        Сбросить
                      </Button>
                    )}
                  </div>
                )}
              </div>
            </div>

            {reason?.reason && (
              <p className="text-[12px] text-[#DCDCDC] leading-snug m-0 break-words">{reason.reason}</p>
            )}

            {(reason?.effort || reason?.impact) && (
              <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-[12px] text-[#929292]">
                {reason.effort && <span>Сложность: <span className="text-[#B8B8B8]">{reason.effort}</span></span>}
                {reason.impact && <span>Эффект: <span className="text-[#B8B8B8]">{reason.impact}</span></span>}
              </div>
            )}

            {(steps.length > 0 || assumptions.length > 0) && (
              <div className="flex flex-col gap-1">
                <button
                  type="button"
                  onClick={() => setOpenSteps((prev) => ({ ...prev, [tid]: !prev[tid] }))}
                  aria-expanded={stepsOpen}
                  className="inline-flex items-center gap-1 text-[12px] text-[#929292] hover:text-[#EAEAEA] w-fit cursor-pointer"
                >
                  {stepsOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                  <span>Этапы ({steps.length})</span>
                </button>
                <CollapseSection open={stepsOpen}>
                  <div className="border-l border-[#303030] pl-2.5 flex flex-col gap-1 text-[12px] pt-0.5">
                    {steps.map((step, stepIdx) => (
                      <div key={stepIdx} className="text-[#DCDCDC]">
                        {stepIdx + 1}. {step.title} ·{' '}
                        <span className="text-[#929292] tabular-nums">
                          {step.min_minutes}–{step.max_minutes} мин{step.wait_minutes > 0 ? ` (+${step.wait_minutes} мин ожидания)` : ''}
                        </span>
                      </div>
                    ))}
                    {assumptions.map((text, aIdx) => (
                      <div key={aIdx} className="text-[11px] text-[#929292]">• {text}</div>
                    ))}
                  </div>
                </CollapseSection>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function AmebaWorkspace({ features = {}, embedded = false }) {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState('periods');
  const [logOpen, setLogOpen] = useState(false);
  const [mobileRisksOpen, setMobileRisksOpen] = useState(false);
  const [wishText, setWishText] = useState('');
  const [submittedWish, setSubmittedWish] = useState('');
  const [wishSuccess, setWishSuccess] = useState(false);

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
  const memory = useQuery({
    queryKey: ['agent-memory'],
    queryFn: () => request('/auth/agent-memory'),
    enabled: features.ameba !== false,
  });

  const refresh = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['tasks'] }),
    queryClient.invalidateQueries({ queryKey: ['ameba-ranking'] }),
    queryClient.invalidateQueries({ queryKey: ['agent-memory'] }),
    queryClient.invalidateQueries({ queryKey: ['calendar'] }),
  ]);

  const rank = useMutation({
    mutationFn: (feedbackText) => {
      const hasText = typeof feedbackText === 'string' && feedbackText.trim().length > 0;
      return agentRequest('/integrations/ameba/rank', {
        method: 'POST',
        body: JSON.stringify(hasText ? { feedback: feedbackText.trim() } : {}),
      });
    },
    onMutate: (feedbackText) => {
      if (typeof feedbackText === 'string' && feedbackText.trim()) {
        setSubmittedWish(feedbackText.trim());
        setWishText('');
        setWishSuccess(false);
      }
    },
    onSuccess: async (res) => {
      setSubmittedWish('');
      if (res?.consumed_feedback) setWishSuccess(true);
      await refresh();
    },
    onError: async (_err, feedbackText) => {
      setSubmittedWish('');
      if (typeof feedbackText === 'string' && feedbackText.trim()) {
        try {
          const latest = await queryClient.fetchQuery({
            queryKey: ['ameba-ranking'],
            queryFn: () => request('/integrations/ameba/ranking'),
          });
          if (!latest?.feedback) {
            setWishText((prev) => prev || feedbackText.trim());
          }
        } catch {
          setWishText((prev) => prev || feedbackText.trim());
        }
      }
    },
  });

  const clearPlan = useMutation({
    mutationFn: () => request('/integrations/ameba/plan', { method: 'DELETE' }),
    onSuccess: async () => {
      rank.reset();
      setSubmittedWish('');
      setWishSuccess(false);
      queryClient.setQueryData(['ameba-live-plan'], null);
      await refresh();
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
  const memoryCount = memory.data?.length ?? 0;

  const mainButtonLabel = isRunning
    ? 'Расчёт плана…'
    : !data?.has_plan
      ? 'Построить план'
      : data?.needs_refresh
        ? 'Пересчитать план'
        : 'Пересчитать';
  const mainButtonVariant = !data?.has_plan || data?.needs_refresh ? 'primary' : 'default';

  const activeWishNotice = isRunning ? (submittedWish || data?.feedback || '') : '';
  const pendingWish = !isRunning ? (data?.feedback || '') : '';
  const restAssumption = activeSchedule?.rest_assumption || data?.calendar_schedule?.rest_assumption;

  function submitWish() {
    const trimmed = wishText.trim();
    if (!trimmed || clearPlan.isPending || queue.data?.browser_busy) return;
    rank.mutate(trimmed);
  }

  const actionsBar = (
    <>
      <Button
        size={actionSize}
        variant={mainButtonVariant}
        leftIcon={<RefreshCw className={`h-3.5 w-3.5 ${isRunning ? 'animate-spin' : ''}`} />}
        loading={rank.isPending && !queue.data?.ameba_running}
        disabled={isRunning || clearPlan.isPending || queue.data?.browser_busy}
        onClick={() => rank.mutate()}
      >
        {mainButtonLabel}
      </Button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <IconButton size={embedded ? 'icon-sm' : 'icon'} variant="default" aria-label="Дополнительные действия">
            <MoreHorizontal className="h-3.5 w-3.5" />
          </IconButton>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem onClick={() => setLogOpen(!logOpen)}>
            <Terminal className="h-3.5 w-3.5" />
            {logOpen ? 'Скрыть журнал Ameba' : 'Журнал Ameba'}
          </DropdownMenuItem>
          {hasManualAdjustments && (
            <DropdownMenuItem disabled={isRunning} onClick={() => adjustPlan.mutate({ reset: true })}>
              <RotateCcw className="h-3.5 w-3.5" />
              Сбросить ручные правки
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
        <div className="relative overflow-hidden flex flex-wrap items-center justify-between gap-2 border border-[#303030] bg-[#111111] px-3 py-2">
          <LiveHorizon active={isRunning || liveActive} tone={status.badge === 'warning' ? 'warning' : status.badge === 'danger' ? 'danger' : 'accent'} />
          <div className="flex items-center gap-2">
            <StatusIndicator tone={status.tone} pulse={status.pulse} />
            <span className="text-[13px] font-semibold text-[#EAEAEA]">Ameba</span>
            <Badge variant={status.badge}>{status.label}</Badge>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">{actionsBar}</div>
        </div>
      )}

      {data && data.enabled === false && (
        <Notice
          tone="warning"
          action={(
            <Button size="xs" variant="outline" asChild>
              <a href="/profile?tab=agents">Открыть настройки агентов →</a>
            </Button>
          )}
        >
          Ameba отключена в личных настройках. Включите её, чтобы строить и обновлять план.
        </Notice>
      )}

      {/* One-time wish box */}
      <section className="border border-[#303030] bg-[#111111] p-3 flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="text-[13px] font-semibold text-[#EAEAEA]">Разовое пожелание к плану</div>
            <div className="text-[11px] text-[#929292]">
              Один раз скорректирует текущий план, а важные предпочтения Ameba сохранит в память.
            </div>
          </div>
          <Button
            size="xs"
            variant={tab === 'memory' ? 'accent-subtle' : 'outline'}
            leftIcon={<Brain className="h-3 w-3" />}
            onClick={() => setTab('memory')}
          >
            Память ({memoryCount})
          </Button>
        </div>

        <div className="flex flex-col sm:flex-row gap-2 items-stretch sm:items-end">
          <Textarea
            rows={2}
            maxLength={4000}
            aria-label="Разовое пожелание к плану"
            placeholder="Например: пятницу сделай облегчённой, сложные задачи ставь до 18:00"
            value={wishText}
            onChange={(event) => setWishText(event.currentTarget.value)}
            onKeyDown={(event) => {
              if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
                event.preventDefault();
                submitWish();
              }
            }}
            className="flex-1"
          />
          <Button
            size="default"
            variant="primary"
            disabled={!wishText.trim() || clearPlan.isPending || queue.data?.browser_busy}
            onClick={submitWish}
            className="shrink-0 max-sm:w-full"
          >
            Учесть в плане
          </Button>
        </div>

        {activeWishNotice && (
          <div className="flex items-center gap-2 border-t border-[#222222] pt-2 text-[12px] text-[#DFC4F8]">
            <StatusIndicator tone="active" pulse />
            <span className="break-words">Ameba учитывает пожелание и сохраняет правила в память: «{activeWishNotice}»</span>
          </div>
        )}

        {pendingWish && (
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[#222222] pt-2 text-[12px]">
            <span className="text-[#D4A24C] break-words">Ожидает применения: «{pendingWish}»</span>
            <div className="flex items-center gap-1.5 shrink-0">
              <Button
                size="xs"
                variant="primary"
                disabled={clearPlan.isPending || queue.data?.browser_busy}
                onClick={() => rank.mutate()}
              >
                Запустить расчёт
              </Button>
              <Button
                size="xs"
                variant="ghost"
                disabled={adjustPlan.isPending}
                onClick={() => adjustPlan.mutate({ clear_feedback: true })}
              >
                Отменить
              </Button>
            </div>
          </div>
        )}
      </section>

      <AgentProgress
        providers={['ameba']}
        active={rank.isPending}
        defaultOpen={false}
        hideSummaryWhenIdle
        open={logOpen}
        onOpenChange={setLogOpen}
      />

      {wishSuccess && (
        <Notice
          tone="success"
          onClose={() => setWishSuccess(false)}
          action={(
            <Button size="xs" variant="outline" onClick={() => setTab('memory')}>
              Проверить память
            </Button>
          )}
        >
          Ameba пересчитала план с учётом пожелания и обновила память.
        </Notice>
      )}

      {rank.data?.unchanged && (
        <Notice tone="info">
          {rank.data.status === 'running' ? 'Ameba уже работает с этими данными.' : 'Данные не изменились — план уже актуален.'}
        </Notice>
      )}
      {error && <Notice tone="error">{error.message}</Notice>}

      {/* Open Plan Summary Card */}
      {data?.has_plan && !data?.plan_reset && (
        <section className="border border-[#303030] bg-[#111111] px-3.5 py-2.5 flex flex-col gap-1.5 text-[12px]">
          {data.explanation && (
            <p className="text-[#EAEAEA] leading-snug m-0 whitespace-pre-wrap break-words">{data.explanation}</p>
          )}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[#929292] tabular-nums">
            {activeSchedule?.hours_label && (
              <span>Объём работ: <strong className="text-[#EAEAEA] font-medium">{activeSchedule.hours_label}</strong></span>
            )}
            <span>
              Задач в плане: <strong className="text-[#EAEAEA] font-medium">{data.ranked_count ?? 0}</strong>
              {data.pending_count > 0 && (
                <span className="text-[#D4A24C]"> (ещё {data.pending_count} ждут разбора Monocle)</span>
              )}
            </span>
            <span>Защищённый резерв: <strong className="text-[#EAEAEA] font-medium">{reservePct}%</strong></span>
            {unfitted.length > 0 && (
              <span className="text-[#D4A24C]">Не поместилось: {unfitted.length}</span>
            )}
            {conflicts.length > 0 && (
              <span className="text-[#D4A24C]">Конфликтов сроков: {conflicts.length}</span>
            )}
          </div>
          {(restAssumption || activeSchedule?.adaptation_summary || data?.observations_count > 0) && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-[#7D7D7D]">
              {restAssumption && <span>{restAssumption}</span>}
              {activeSchedule?.adaptation_summary && <span>· {activeSchedule.adaptation_summary}</span>}
              {data.observations_count > 0 && <span className="tabular-nums">· Откликов: {data.observations_count}</span>}
            </div>
          )}
          {hasManualAdjustments && (
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[#222222] pt-1.5 mt-0.5">
              <span className="text-[11px] text-[#6FA3CC]">Применены ручные правки дней, переносов или остатков времени.</span>
              <Button
                size="xs"
                variant="outline"
                leftIcon={<RotateCcw className="h-3 w-3" />}
                disabled={isRunning || adjustPlan.isPending}
                onClick={() => adjustPlan.mutate({ reset: true })}
              >
                Сбросить ручные правки
              </Button>
            </div>
          )}
        </section>
      )}

      {/* Tabs + Conditional Risks Sidebar */}
      {(() => {
        const risksContent = hasSidebar ? (
          <>
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
          </>
        ) : null;

        const riskParts = [
          unfitted.length > 0 ? `Не поместилось: ${unfitted.length}` : null,
          conflicts.length > 0 ? `Риски сроков: ${conflicts.length}` : null,
        ].filter(Boolean);

        return (
          <div className={hasSidebar ? 'grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_300px] gap-4 items-start' : 'grid grid-cols-1'}>
            {hasSidebar && (
              <div className="xl:hidden flex flex-col gap-2">
                <button
                  type="button"
                  aria-expanded={mobileRisksOpen}
                  onClick={() => setMobileRisksOpen((prev) => !prev)}
                  className="w-full border border-[#4A391D] border-l-2 border-l-[#D4A24C] bg-[#1B160D] px-3 py-2 flex items-center justify-between gap-2 text-left text-[12px] text-[#EAEAEA] cursor-pointer"
                >
                  <span className="inline-flex items-center gap-2 min-w-0 font-medium">
                    <AlertTriangle className="h-3.5 w-3.5 text-[#D4A24C] shrink-0" aria-hidden="true" />
                    <span className="truncate">{riskParts.join(' · ')}</span>
                  </span>
                  {mobileRisksOpen ? <ChevronDown className="h-4 w-4 text-[#929292] shrink-0" /> : <ChevronRight className="h-4 w-4 text-[#929292] shrink-0" />}
                </button>
                <CollapseSection open={mobileRisksOpen}>
                  <div className="flex flex-col gap-3">
                    {risksContent}
                  </div>
                </CollapseSection>
              </div>
            )}

            <Tabs value={tab} onValueChange={setTab} className="min-w-0">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#303030]">
                <TabsList className="border-b-0">
                  <TabsTrigger value="periods">План по дням</TabsTrigger>
                  <TabsTrigger value="tasks">
                    Приоритет задач{data?.ranked_count != null ? ` (${data.ranked_count})` : ''}
                  </TabsTrigger>
                  <TabsTrigger value="load">Нагрузка по дням</TabsTrigger>
                  <TabsTrigger value="memory">Память ({memoryCount})</TabsTrigger>
                </TabsList>
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

              <TabsContent value="tasks">
                <TabTransition transitionKey="tasks">
                  <PriorityTasksTab
                    reasons={data?.reasons ?? []}
                    timeline={activeTimeline}
                    taskIds={data?.task_ids ?? []}
                    manualOverrides={manual.estimate_overrides ?? {}}
                    onAdjust={(payload) => adjustPlan.mutate(payload)}
                    busy={adjustPlan.isPending || isRunning}
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

              <TabsContent value="memory">
                <TabTransition transitionKey="memory">
                  <div className="flex flex-col gap-3">
                    <p className="text-[12px] text-[#929292] m-0">
                      Устойчивые факты и предпочтения, которые учитывает Ameba и другие агенты. Разовые пожелания сохраняются сюда.
                    </p>
                    <AgentMemory />
                  </div>
                </TabTransition>
              </TabsContent>
            </Tabs>

            {hasSidebar && (
              <aside className="hidden xl:flex flex-col gap-3">
                {risksContent}
              </aside>
            )}
          </div>
        );
      })()}
    </div>
  );
}
