import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowRight,
  CalendarDays,
  ChevronDown,
  ChevronRight,
  MoreHorizontal,
  RefreshCw,
  RotateCcw,
  Terminal,
  Trash2,
} from 'lucide-react';
import { agentRequest, request } from './api';
import AgentTimeline, { durationLabel, formatShortDate } from './AgentTimeline';
import AgentProgress from './AgentProgress';
import { Button, IconButton } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Textarea } from '@/components/ui/input';
import { Badge, Notice, PageHeader, StatusIndicator } from '@/components/ui/surface';
import { HintTooltip } from '@/components/ui/tooltip';
import { CollapseSection, LiveHorizon } from '@/lib/motion';

const WEEKDAYS_RU = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];

function resolvePlanStatus({ rankingData, liveActive, isRunning, rankError, failedRun }) {
  if (isRunning || liveActive) return { tone: 'active', pulse: true, badge: 'accent', label: 'Составляет план' };
  if (rankError || failedRun) return { tone: 'danger', pulse: false, badge: 'danger', label: 'Ошибка' };
  if (!rankingData?.has_plan || rankingData?.plan_reset) return { tone: 'idle', pulse: false, badge: 'outline', label: 'Нет плана' };
  const conflicts = rankingData?.schedule?.deadline_conflicts ?? [];
  if (!rankingData?.schedule?.feasible || conflicts.length > 0) {
    return { tone: 'warning', pulse: false, badge: 'warning', label: rankingData.needs_refresh ? 'Устарел · риск' : 'Есть риск' };
  }
  if (rankingData?.needs_refresh) return { tone: 'warning', pulse: false, badge: 'warning', label: 'Устарел' };
  return { tone: 'success', pulse: false, badge: 'success', label: 'Актуален' };
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
  const focusPeriod = periods.find((p) => (p.kind === 'work' || p.tasks?.length > 0)) ?? periods[0];
  const horizonTone = status.badge === 'warning' ? 'warning' : status.badge === 'danger' ? 'danger' : 'accent';
  const actionLabel = isRunning ? 'Думает…' : !data?.has_plan ? 'Построить план' : 'Обновить';
  const actionVariant = !data?.has_plan || data?.needs_refresh ? 'primary' : 'default';

  return (
    <div className="relative overflow-hidden border border-[#303030] bg-[#111111] px-3 py-2 flex flex-wrap items-center justify-between gap-2">
      <LiveHorizon active={isRunning || liveActive} tone={horizonTone} />
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 min-w-0 text-[12px]">
        <div className="flex items-center gap-2 shrink-0">
          <StatusIndicator tone={status.tone} pulse={status.pulse} />
          <span className="font-semibold text-[#EAEAEA]">Ameba</span>
          {(isRunning || status.badge !== 'success') && (
            <Badge variant={status.badge}>{status.label}</Badge>
          )}
        </div>

        {focusPeriod && (
          <span className="text-[#B8B8B8] truncate max-w-[300px]">{focusPeriod.title}</span>
        )}

        {conflicts.length > 0 && (
          <HintTooltip label={conflicts.map((c) => c.reason).join('\n')}>
            <Badge variant="warning" className="cursor-help">
              <AlertTriangle className="h-3 w-3" />
              Есть риск ({conflicts.length})
            </Badge>
          </HintTooltip>
        )}

        {data?.pending_count > 0 && (
          <span className="text-[#929292] tabular-nums">· ждут Monocle: {data.pending_count}</span>
        )}
      </div>

      <div className="flex items-center gap-1.5 shrink-0 max-sm:w-full">
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

export default function AmebaWorkspace({ features = {}, embedded = false }) {
  const queryClient = useQueryClient();
  const [logOpen, setLogOpen] = useState(false);
  const [calendarOpen, setCalendarOpen] = useState(false);
  const [risksOpen, setRisksOpen] = useState(false);
  const [wishText, setWishText] = useState('');
  const [wishResult, setWishResult] = useState(null);

  const ranking = useQuery({
    queryKey: ['ameba-ranking'],
    queryFn: () => request('/integrations/ameba/ranking'),
    enabled: features.ameba !== false,
    refetchInterval: 15000,
  });
  const queue = useQuery({
    queryKey: ['agent-queue'],
    queryFn: () => request('/integrations/queue'),
    enabled: features.monocle !== false || features.straus !== false || features.arrodes !== false || features.ameba !== false,
    refetchInterval: (query) => (query.state.data?.busy ? 1500 : 5000),
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
        setWishText('');
        setWishResult(null);
      }
    },
    onSuccess: async (res) => {
      if (res?.consumed_feedback) {
        setWishResult({
          memorySaved: Number(res.memory_saved_count || 0),
          progressUpdated: Number(res.progress_updated_count || 0),
        });
      }
      await refresh();
    },
    onError: async (_err, feedbackText) => {
      if (typeof feedbackText === 'string' && feedbackText.trim()) {
        setWishText((prev) => prev || feedbackText.trim());
      }
    },
  });

  const clearPlan = useMutation({
    mutationFn: () => request('/integrations/ameba/plan', { method: 'DELETE' }),
    onSuccess: async () => {
      rank.reset();
      setWishResult(null);
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
  const manual = data?.manual_adjustments ?? {};
  const hasManualAdjustments = Boolean(
    Object.keys(manual.day_overrides ?? {}).length
    || Object.keys(manual.task_pins ?? {}).length
    || Object.keys(manual.estimate_overrides ?? {}).length
    || Object.keys(manual.task_notes ?? {}).length,
  );
  const isRunning = Boolean(queue.data?.ameba_running || rank.isPending);
  const status = resolvePlanStatus({
    rankingData: data,
    liveActive,
    isRunning,
    rankError: rank.error,
    failedRun: ranking.error,
  });

  const conflicts = activeSchedule?.deadline_conflicts ?? [];
  const unfitted = (activeSchedule?.remaining ?? []).filter((r) => r.remaining_work_minutes > 0);
  const conflictIds = new Set(conflicts.map((c) => c.task_id));
  const extraUnfitted = unfitted.filter((u) => !conflictIds.has(u.task_id));
  const hasRisks = conflicts.length > 0 || extraUnfitted.length > 0;
  const timelineTasks = (liveActive ? livePlan.data?.timeline : null) ?? data?.timeline ?? [];
  const titleMap = new Map(timelineTasks.map((item) => [item.id ?? item.task_id, item.title]));
  const error = ranking.error || rank.error || clearPlan.error || feedback.error || adjustPlan.error;
  const previewDays = (activeSchedule?.daily_allocations ?? data?.calendar_schedule?.days_summary ?? []).slice(0, 7);

  const mainButtonLabel = isRunning
    ? 'Составляет план…'
    : !data?.has_plan
      ? 'Построить план'
      : 'Обновить';
  const mainButtonVariant = !data?.has_plan || data?.needs_refresh ? 'primary' : 'default';

  function submitWish() {
    const trimmed = wishText.trim();
    if (!trimmed || isRunning || clearPlan.isPending || queue.data?.browser_busy) return;
    rank.mutate(trimmed);
  }

  const actionsBar = (
    <>
      <Button
        size={embedded ? 'sm' : 'default'}
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
          <IconButton size={embedded ? 'icon-sm' : 'icon'} variant="default" aria-label="Дополнительно">
            <MoreHorizontal className="h-3.5 w-3.5" />
          </IconButton>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onClick={() => setLogOpen(!logOpen)}>
            <Terminal className="h-3.5 w-3.5" />
            {logOpen ? 'Скрыть журнал' : 'Журнал работы'}
          </DropdownMenuItem>
          {previewDays.length > 0 && (
            <DropdownMenuItem onClick={() => setCalendarOpen(!calendarOpen)}>
              <CalendarDays className="h-3.5 w-3.5" />
              {calendarOpen ? 'Скрыть занятость недели' : 'Занятость недели'}
            </DropdownMenuItem>
          )}
          {hasManualAdjustments && (
            <DropdownMenuItem disabled={isRunning} onClick={() => adjustPlan.mutate({ reset: true })}>
              <RotateCcw className="h-3.5 w-3.5" />
              Сбросить корректировки
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            danger
            disabled={!data?.has_plan || isRunning || clearPlan.isPending}
            onClick={() => {
              if (window.confirm('Удалить сохранённый план Ameba?')) clearPlan.mutate();
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
    <div className="flex flex-col gap-3 max-w-[780px]">
      {!embedded ? (
        <PageHeader
          title="Ameba · План"
          badge={(isRunning || status.badge !== 'success') ? <Badge variant={status.badge}>{status.label}</Badge> : undefined}
          actions={actionsBar}
        />
      ) : (
        <div className="relative overflow-hidden flex flex-wrap items-center justify-between gap-2 border border-[#303030] bg-[#111111] px-3 py-2">
          <LiveHorizon active={isRunning || liveActive} tone={status.badge === 'warning' ? 'warning' : status.badge === 'danger' ? 'danger' : 'accent'} />
          <div className="flex items-center gap-2">
            <StatusIndicator tone={status.tone} pulse={status.pulse} />
            <span className="text-[13px] font-semibold text-[#EAEAEA]">Ameba · План</span>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">{actionsBar}</div>
        </div>
      )}

      {data && data.enabled === false && (
        <Notice
          tone="warning"
          action={(
            <Button size="xs" variant="outline" asChild>
              <a href="/profile?tab=agents">Настройки агентов →</a>
            </Button>
          )}
        >
          Ameba отключена в настройках.
        </Notice>
      )}

      {Boolean(queue.data?.agents_busy ?? queue.data?.browser_busy) && !isRunning && (
        <Notice tone="info">
          Сейчас работают другие агенты — Ameba запустится сразу после их завершения.
        </Notice>
      )}

      <AgentProgress
        providers={['ameba']}
        active={rank.isPending}
        autoOpenOnActive={false}
        defaultOpen={false}
        hideSummaryWhenIdle
        open={logOpen}
        onOpenChange={setLogOpen}
      />

      {error && <Notice tone="error">{error.message}</Notice>}

      {rank.data?.unchanged && (
        <Notice tone="info">
          {rank.data.status === 'running' ? 'Ameba уже работает с этими данными.' : 'Изменений нет — план уже актуален.'}
        </Notice>
      )}

      {wishResult && (
        <Notice
          tone="success"
          onClose={() => setWishResult(null)}
          action={wishResult.memorySaved > 0 ? (
            <Button size="xs" variant="outline" asChild>
              <a href="/profile?tab=memory">Память →</a>
            </Button>
          ) : undefined}
        >
          {wishResult.memorySaved > 0
            ? 'План обновлён; устойчивое предпочтение сохранено в память.'
            : wishResult.progressUpdated > 0
              ? 'План обновлён с учётом выполненного времени.'
              : 'План обновлён с учётом пожелания.'}
        </Notice>
      )}

      {data?.feedback && !isRunning && (
        <Notice
          tone="warning"
          action={(
            <div className="flex items-center gap-1.5">
              <Button size="xs" variant="primary" onClick={() => rank.mutate()}>Применить</Button>
              <Button size="xs" variant="ghost" onClick={() => adjustPlan.mutate({ clear_feedback: true })}>Сбросить</Button>
            </div>
          )}
        >
          Ожидает применения: «{data.feedback}»
        </Notice>
      )}

      <CollapseSection open={calendarOpen}>
        {previewDays.length > 0 && (
          <div className="border border-[#262626] bg-[#0D0D0D] p-2.5 grid grid-cols-2 sm:grid-cols-4 md:grid-cols-7 gap-1.5">
            {previewDays.map((day) => {
              const dt = new Date(`${day.date}T12:00:00`);
              return (
                <div key={day.date} className="border border-[#222222] bg-[#111111] px-2 py-1.5 flex flex-col gap-0.5">
                  <div className="text-[11px] font-medium text-[#EAEAEA]">
                    {WEEKDAYS_RU[dt.getDay()]} · {formatShortDate(day.date)}
                  </div>
                  <div className="text-[11px] text-[#929292] tabular-nums">
                    {day.busy_minutes > 0 ? `Занято ${durationLabel(day.busy_minutes)}` : 'Свободно'}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CollapseSection>

      {data?.has_plan && !data?.plan_reset && (data.explanation || data.pending_count > 0) && (
        <div className="text-[12px] text-[#B8B8B8] leading-snug px-0.5 flex flex-col gap-1">
          {data.explanation && <p className="m-0 break-words">{data.explanation}</p>}
          {data.pending_count > 0 && (
            <span className="text-[11px] text-[#929292]">
              Ещё {data.pending_count} задач ожидают разбора Monocle.
            </span>
          )}
        </div>
      )}

      {hasRisks && (
        <section className="border border-[#4A391D] border-l-2 border-l-[#D4A24C] bg-[#1B160D] px-3 py-2 flex flex-col gap-1.5">
          <button
            type="button"
            aria-expanded={risksOpen}
            onClick={() => setRisksOpen((prev) => !prev)}
            className="w-full flex items-center justify-between gap-2 text-left text-[12px] text-[#EAEAEA] cursor-pointer"
          >
            <span className="inline-flex items-center gap-2 min-w-0 font-medium">
              <AlertTriangle className="h-3.5 w-3.5 text-[#D4A24C] shrink-0" aria-hidden="true" />
              <span className="truncate">
                {conflicts.length > 0
                  ? `${conflicts[0].title}: есть риск не успеть${conflicts.length + extraUnfitted.length > 1 ? ` (+ ещё ${conflicts.length + extraUnfitted.length - 1})` : ''}`
                  : `Часть задач не поместилась в план (${extraUnfitted.length})`}
              </span>
            </span>
            {risksOpen ? <ChevronDown className="h-4 w-4 text-[#929292] shrink-0" /> : <ChevronRight className="h-4 w-4 text-[#929292] shrink-0" />}
          </button>

          <CollapseSection open={risksOpen}>
            <div className="pt-1 border-t border-[#4A391D]/60 flex flex-col gap-2 text-[12px]">
              {conflicts.map((c) => (
                <div key={c.task_id} className="flex flex-col gap-0.5">
                  <a href={`/?task=${c.task_id}`} className="font-medium text-[#EAEAEA] hover:underline w-fit">
                    {c.title || titleMap.get(c.task_id) || `Задача #${c.task_id}`}
                  </a>
                  <span className="text-[#DCDCDC]">{c.reason}</span>
                </div>
              ))}
              {extraUnfitted.map((item) => (
                <div key={item.task_id} className="flex items-center justify-between gap-2">
                  <a href={`/?task=${item.task_id}`} className="text-[#EAEAEA] hover:underline truncate">
                    {titleMap.get(item.task_id) ?? `Задача #${item.task_id}`}
                  </a>
                  <span className="text-[#D4A24C] tabular-nums shrink-0">
                    ост. {durationLabel(item.remaining_work_minutes)}
                  </span>
                </div>
              ))}
            </div>
          </CollapseSection>
        </section>
      )}

      <AgentTimeline
        schedule={activeSchedule}
        calendarOnly={!activeSchedule}
        finished={!isRunning && !livePlan.data?.running}
        busy={feedback.isPending || adjustPlan.isPending || isRunning}
        onFeedback={(payload) => feedback.mutate(payload)}
        onAdjust={(payload) => adjustPlan.mutate(payload)}
        taskPins={manual.task_pins ?? {}}
        estimateOverrides={manual.estimate_overrides ?? {}}
        onRefresh={() => rank.mutate()}
      />

      <section className="border border-[#303030] bg-[#111111] p-3 flex flex-col gap-2">
        <label htmlFor="ameba-wish-input" className="text-[12px] font-medium text-[#EAEAEA]">
          Что изменить в плане?
        </label>
        <div className="flex flex-col sm:flex-row gap-2 items-stretch sm:items-end">
          <Textarea
            id="ameba-wish-input"
            rows={2}
            maxLength={4000}
            placeholder="Например: освободи пятницу, переезд растяни на неделю, уже сделал половину лабораторной"
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
            disabled={!wishText.trim() || isRunning || clearPlan.isPending || queue.data?.browser_busy}
            onClick={submitWish}
            className="shrink-0 max-sm:w-full"
          >
            Применить
          </Button>
        </div>
      </section>
    </div>
  );
}
