import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowRight,
  ChevronDown,
  ChevronRight,
  MoreHorizontal,
  RefreshCw,
  RotateCcw,
  Terminal,
  Trash2,
} from 'lucide-react';
import { agentRequest, request } from './api';
import AgentTimeline, { durationLabel } from './AgentTimeline';
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
import { Badge, Notice, PageHeader, SectionHeader, StatusIndicator } from '@/components/ui/surface';
import { HintTooltip } from '@/components/ui/tooltip';
import { CollapseSection, LiveHorizon } from '@/lib/motion';

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

export default function AmebaWorkspace({ features = {}, embedded = false }) {
  const queryClient = useQueryClient();
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
          Ameba отключена в настройках.
        </Notice>
      )}

      {/* One-time wish box */}
      <section className="border border-[#303030] bg-[#111111] p-3 flex flex-col gap-2">
        <div className="text-[13px] font-semibold text-[#EAEAEA]">Разовое пожелание к плану</div>

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
            <Button size="xs" variant="outline" asChild>
              <a href="/profile?tab=memory">Проверить память →</a>
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
          {(activeSchedule?.adaptation_summary || data?.observations_count > 0) && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-[#7D7D7D]">
              {activeSchedule?.adaptation_summary && <span>{activeSchedule.adaptation_summary}</span>}
              {data.observations_count > 0 && (
                <span className="tabular-nums">
                  {activeSchedule?.adaptation_summary ? '· ' : ''}Откликов: {data.observations_count}
                </span>
              )}
            </div>
          )}
          {hasManualAdjustments && (
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[#222222] pt-1.5 mt-0.5">
              <span className="text-[11px] text-[#6FA3CC]">Есть ручные правки.</span>
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

            <div className="min-w-0">
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
            </div>

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
