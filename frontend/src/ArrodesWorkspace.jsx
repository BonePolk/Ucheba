import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowUpRight,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Download,
  FolderOpen,
  Play,
  RotateCcw,
  Square,
  Terminal,
} from 'lucide-react';
import { request } from './api';
import { AgentLog, isBrowserActiveInRun, useAgentProgressStream } from './AgentProgress';
import AgentQuestion from './AgentQuestion';
import ContextFiles from './ContextFiles';
import MonocleBrowser from './MonocleBrowser';
import TaskMarkdown from './TaskMarkdown';
import { Button } from '@/components/ui/button';
import { Field, Textarea } from '@/components/ui/input';
import { Combobox } from '@/components/ui/select';
import { Badge, EmptyState, Notice, SectionHeader, StatusIndicator } from '@/components/ui/surface';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, TableContainer } from '@/components/ui/table';
import { AnimatePresence, CollapseSection, ExpandableText, LiveHorizon, motion, PANEL_TRANSITION, StageSwap, useReducedMotion } from '@/lib/motion';

const runStatusMeta = {
  queued: { label: 'В очереди', variant: 'info' },
  running: { label: 'Работает', variant: 'accent' },
  succeeded: { label: 'На проверку', variant: 'success' },
  failed: { label: 'Ошибка', variant: 'danger' },
  interrupted: { label: 'Прервано', variant: 'warning' },
};

function RunFiles({ run, defaultOpen = false }) {
  const [opened, setOpened] = useState(defaultOpen);
  const files = useQuery({
    queryKey: ['integrations', 'files', run.id],
    queryFn: () => request(`/integrations/arrodes/runs/${run.id}/files`),
    enabled: opened,
  });

  return (
    <div className="flex flex-col gap-1.5">
      <button
        type="button"
        onClick={() => setOpened(!opened)}
        aria-expanded={opened}
        className="inline-flex items-center gap-1.5 text-[12px] text-[#929292] hover:text-[#EAEAEA] w-fit cursor-pointer"
      >
        {opened ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        <FolderOpen className="h-3.5 w-3.5" />
        <span>Файлы результата</span>
        {files.data && <span className="text-[11px] tabular-nums">({files.data.length})</span>}
      </button>

      <CollapseSection open={opened}>
        <div className="border border-[#262626] bg-[#0D0D0D] p-2 flex flex-col gap-1">
          {files.isFetching && <div className="text-[12px] text-[#929292]">Загрузка…</div>}
          {files.error && <Notice tone="error">{files.error.message}</Notice>}
          {files.data?.length === 0 && <div className="text-[12px] text-[#929292]">Файлов нет</div>}
          {files.data?.map((name) => (
            <a
              key={name}
              href={`/api/integrations/arrodes/runs/${run.id}/files/${name.split('/').map(encodeURIComponent).join('/')}`}
              download
              className="inline-flex items-center gap-1.5 text-[12px] max-md:text-[13px] text-[#CFA9F5] hover:underline py-0.5 max-md:py-2 truncate"
            >
              <Download className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{name}</span>
            </a>
          ))}
        </div>
      </CollapseSection>
    </div>
  );
}

function RunResultCard({ run, disabled, onContinue, defaultFilesOpen = false }) {
  const status = runStatusMeta[run.status] ?? { label: run.status, variant: 'default' };
  return (
    <div className="border border-[#303030] bg-[#111111] p-3 flex flex-col gap-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#222222] pb-2">
        <div className="flex items-center gap-2">
          <Badge variant={status.variant}>{status.label}</Badge>
          <span className="text-[11px] text-[#929292] tabular-nums">{new Date(run.created_at).toLocaleString()}</span>
        </div>
        <Button size="xs" variant="outline" leftIcon={<RotateCcw className="h-3 w-3" />} disabled={disabled} onClick={() => onContinue(run.id)}>
          Продолжить
        </Button>
      </div>
      {run.summary && (
        <ExpandableText text={run.summary} threshold={520} collapsedHeight="max-h-[220px]">
          <TaskMarkdown>{run.summary}</TaskMarkdown>
        </ExpandableText>
      )}
      {run.error && <Notice tone="error">{run.error}</Notice>}
      <RunFiles run={run} defaultOpen={defaultFilesOpen} />
    </div>
  );
}

export default function ArrodesWorkspace({ task: fixedTask = null, features = {}, compact = false, initialTaskId = null }) {
  const client = useQueryClient();
  const reduced = useReducedMotion();
  const [selectedTaskId, setSelectedTaskId] = useState(() => (
    fixedTask?.id ?? (initialTaskId ? Number(initialTaskId) : null)
  ));
  const [instruction, setInstruction] = useState('');
  const [resumeRunId, setResumeRunId] = useState(null);
  const [materialsOpen, setMaterialsOpen] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const [viewedRunId, setViewedRunId] = useState(null);

  const tasksQuery = useQuery({
    queryKey: ['tasks', 'arrodes-picker'],
    queryFn: () => request('/tasks?archived=false'),
    enabled: !fixedTask,
  });

  const resolvedTaskId = fixedTask?.id ?? selectedTaskId;

  const taskDetailQuery = useQuery({
    queryKey: ['tasks', String(resolvedTaskId)],
    queryFn: () => request(`/tasks/${resolvedTaskId}`),
    enabled: !fixedTask && Boolean(resolvedTaskId),
  });

  const selectedTask = fixedTask ?? taskDetailQuery.data ?? (tasksQuery.data ?? []).find((t) => t.id === resolvedTaskId) ?? null;

  const arrodesStatus = useQuery({
    queryKey: ['integrations', 'arrodes'],
    queryFn: () => request('/integrations/arrodes/status'),
    enabled: features.arrodes !== false,
    refetchInterval: 15000,
    retry: false,
  });
  const integrations = useQuery({
    queryKey: ['integrations'],
    queryFn: () => request('/integrations'),
    enabled: features.arrodes !== false,
    retry: false,
  });

  const contextQuery = useQuery({
    queryKey: ['context', selectedTask?.id],
    queryFn: () => request(`/integrations/tasks/${selectedTask.id}/monocle`),
    enabled: Boolean(selectedTask?.id) && features.monocle !== false,
    refetchInterval: 5000,
  });
  const filesQuery = useQuery({
    queryKey: ['context-files', selectedTask?.id],
    queryFn: () => request(`/integrations/tasks/${selectedTask.id}/monocle/files`),
    enabled: Boolean(selectedTask?.id) && features.monocle !== false,
  });
  const runs = useQuery({
    queryKey: ['integrations', 'runs', selectedTask?.id],
    queryFn: () => request(`/integrations/tasks/${selectedTask.id}/runs`),
    enabled: Boolean(selectedTask?.id) && features.arrodes !== false,
    refetchInterval: 4000,
    retry: false,
  });

  // Live SSE progress for Arrodes in standalone view; in compact task mode unified AgentProgress handles live output.
  const { entries: streamEntries } = useAgentProgressStream(selectedTask?.id ?? 0, compact ? [] : ['arrodes']);
  const liveEntry = streamEntries.find((e) => e.provider === 'arrodes');
  const liveRun = liveEntry?.run;

  useEffect(() => {
    if (!fixedTask && !selectedTaskId && liveRun?.task_id) {
      setSelectedTaskId(liveRun.task_id);
    }
  }, [fixedTask, selectedTaskId, liveRun?.task_id]);

  const start = useMutation({
    mutationFn: () => request(`/integrations/tasks/${selectedTask.id}/arrodes`, {
      method: 'POST',
      body: JSON.stringify({ instruction, resume_run_id: resumeRunId }),
    }),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['integrations', 'runs', selectedTask.id] }),
        client.invalidateQueries({ queryKey: ['tasks'] }),
      ]);
    },
  });

  const stop = useMutation({
    mutationFn: () => request('/integrations/arrodes/stop', { method: 'POST' }),
    onSettled: () => client.invalidateQueries({ queryKey: ['integrations', 'runs', selectedTask?.id] }),
  });

  const confirmDone = useMutation({
    mutationFn: () => request(`/tasks/${selectedTask.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status: 'done' }),
    }),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['tasks'] }),
        client.invalidateQueries({ queryKey: ['tasks', String(selectedTask.id)] }),
        client.invalidateQueries({ queryKey: ['ameba-ranking'] }),
      ]);
    },
  });

  const arrodesRunning = Boolean(
    runs.data?.some((run) => ['queued', 'running'].includes(run.status))
    || liveRun?.status === 'running',
  );
  const completedRuns = (runs.data ?? []).filter((run) => !['queued', 'running'].includes(run.status));
  const activeResult = completedRuns.find((r) => r.id === viewedRunId) ?? completedRuns[0] ?? null;

  const significantEvents = (liveRun?.events ?? [])
    .filter((ev) => ev.kind === 'note' || ev.kind === 'summary' || ev.description)
    .slice(-5);
  const browserActive = isBrowserActiveInRun(liveEntry);

  const authMode = integrations.data?.arrodes?.auth_mode || 'oauth';
  const modelConfigured = Boolean((integrations.data?.arrodes?.model || '').trim());
  const apiKeyReady = Boolean(integrations.data?.ai_provider?.configured?.api_key && modelConfigured);
  const antigravityReady = Boolean(integrations.data?.antigravity_gateway?.base_url?.trim() && modelConfigured);
  const oauthReady = Boolean(arrodesStatus.data?.authorized);
  const authReady = authMode === 'api_key' ? apiKeyReady : authMode === 'antigravity_gateway' ? antigravityReady : oauthReady;
  const notAuthorized = authMode === 'api_key'
    ? Boolean(integrations.data && !apiKeyReady)
    : authMode === 'antigravity_gateway'
      ? Boolean(integrations.data && !antigravityReady)
      : Boolean(arrodesStatus.data && !oauthReady);
  const sandboxBlocked = arrodesStatus.data && !arrodesStatus.data.sandbox_available;
  const configuredModel = integrations.data?.arrodes?.model;
  const liveActiveCard = Boolean(
    !compact
    && selectedTask
    && (arrodesRunning || liveRun?.status === 'running' || liveRun?.pending_question || liveRun?.status === 'failed'),
  );
  const liveHasTokens = Boolean(liveRun?.token_usage && (liveRun.token_usage.input_tokens != null || liveRun.token_usage.output_tokens != null));
  const stageKey = liveRun?.pending_question
    ? 'question'
    : liveRun?.browser_waiting
      ? 'browser-wait'
      : liveRun?.model_waiting
        ? 'thinking'
        : (liveRun?.current_action ?? 'prep');

  if (features.arrodes === false) {
    return <EmptyState title="Arrodes отключён" />;
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Task selection & environment bar */}
      <div className={compact ? 'flex flex-col gap-2.5' : 'border border-[#303030] bg-[#111111] p-3 flex flex-col gap-2.5'}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            {!compact && <Terminal className="h-4 w-4 text-[#B98CE8] shrink-0" aria-hidden="true" />}
            {!compact && <span className="text-[14px] font-semibold text-[#EAEAEA]">Arrodes</span>}
            {(authMode === 'oauth' ? arrodesStatus.data : integrations.data) && (
              <Badge variant={authReady ? 'success' : 'warning'}>
                {authMode === 'api_key'
                  ? (authReady ? 'API-ключ' : 'Нет ключа')
                  : authMode === 'antigravity_gateway'
                    ? (authReady ? 'Antigravity Gateway' : 'Нет шлюза')
                    : (authReady ? 'Подключён' : 'Нет входа')}
              </Badge>
            )}
            {configuredModel && <Badge variant="outline">{configuredModel}</Badge>}
          </div>
          {compact && selectedTask && (
            <a href={`/agents?tab=arrodes&task=${selectedTask.id}`} className="inline-flex items-center gap-1 text-[11px] text-[#B98CE8] hover:underline">
              Полный экран <ArrowUpRight className="h-3 w-3" />
            </a>
          )}
        </div>

        {!fixedTask && (
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Задача" className="w-full min-w-0 sm:flex-1 sm:min-w-[220px]">
              <Combobox
                placeholder="Выберите задачу…"
                searchPlaceholder="Поиск по задачам…"
                value={selectedTaskId ? String(selectedTaskId) : null}
                onChange={(val) => {
                  setSelectedTaskId(val ? Number(val) : null);
                  setResumeRunId(null);
                  setViewedRunId(null);
                }}
                options={(tasksQuery.data ?? []).map((t) => ({ value: String(t.id), label: `#${t.id} ${t.title}` }))}
              />
            </Field>
            {selectedTask && (
              <Button size="default" variant="outline" asChild>
                <a href={`/?task=${selectedTask.id}`}>
                  Задача <ArrowUpRight className="h-3.5 w-3.5" />
                </a>
              </Button>
            )}
          </div>
        )}

        {notAuthorized && (
          <Notice tone="warning" action={<Button size="xs" asChild><a href="/profile?tab=agents">Настроить</a></Button>}>
            {authMode === 'api_key'
              ? 'Сохраните API-ключ и модель Arrodes в настройках агентов.'
              : authMode === 'antigravity_gateway'
                ? 'Укажите адрес Antigravity Gateway и модель Arrodes в настройках агентов.'
                : 'Нужна авторизация OpenAI OAuth в настройках агентов.'}
          </Notice>
        )}

        {sandboxBlocked && (
          <Notice tone="error">
            Sandbox недоступен: {arrodesStatus.data.sandbox_error || 'выполнение заблокировано'}.
          </Notice>
        )}

        {!selectedTask ? (
          <EmptyState icon={<Bot className="h-5 w-5" />} title="Выберите задачу" />
        ) : (
          <>
            {/* Pre-run materials bar */}
            {!compact && (
              <div className="border border-[#242424] bg-[#0D0D0D] px-2.5 py-2 flex flex-col gap-1.5 text-[12px]">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2 min-w-0">
                    <Badge variant={contextQuery.data?.status === 'ready' ? 'success' : 'outline'}>
                      Контекст: {contextQuery.data?.status === 'ready' ? 'готов' : contextQuery.data?.status === 'needs_context' ? 'уточнения' : 'нет'}
                    </Badge>
                    <Badge variant="outline">Файлы: {filesQuery.data?.length ?? 0}</Badge>
                  </div>
                  <button
                    type="button"
                    onClick={() => setMaterialsOpen(!materialsOpen)}
                    className="inline-flex items-center gap-1 text-[11px] text-[#CFA9F5] hover:underline cursor-pointer"
                  >
                    {materialsOpen ? 'Скрыть вложения' : 'Вложения'}
                  </button>
                </div>
                <CollapseSection open={materialsOpen}>
                  <div className="pt-2 border-t border-[#222222] flex flex-col gap-2">
                    {features.monocle !== false && <ContextFiles taskId={selectedTask.id} disabled={arrodesRunning} />}
                  </div>
                </CollapseSection>
              </div>
            )}

            {/* Instruction & Controls */}
            <div className="flex flex-col gap-2">
              {resumeRunId && (
                <div className="flex items-center justify-between gap-2 bg-[#17121E] border border-[#543476] px-2.5 py-1.5 text-[12px]">
                  <span className="text-[#CFA9F5]">Продолжение запуска #{resumeRunId}</span>
                  <Button size="xs" variant="ghost" disabled={arrodesRunning} onClick={() => setResumeRunId(null)}>
                    Сбросить
                  </Button>
                </div>
              )}

              <Field label={resumeRunId ? 'Правки / продолжение' : 'Поручение'}>
                <Textarea
                  rows={compact ? 2 : 3}
                  maxLength={12000}
                  placeholder={resumeRunId ? 'Что изменить или продолжить…' : 'Что выполнить по задаче…'}
                  value={instruction}
                  onChange={(event) => setInstruction(event.currentTarget.value)}
                />
              </Field>

              <div className="flex flex-wrap items-center gap-1.5">
                <Button
                  variant="primary"
                  leftIcon={<Play className="h-3.5 w-3.5" />}
                  loading={start.isPending}
                  disabled={start.isPending || arrodesRunning || Boolean(sandboxBlocked)}
                  onClick={() => start.mutate()}
                >
                  {resumeRunId ? 'Продолжить' : 'Запустить'}
                </Button>
                {arrodesRunning && (
                  <Button
                    variant="danger"
                    leftIcon={<Square className="h-3.5 w-3.5" />}
                    loading={stop.isPending}
                    onClick={() => stop.mutate()}
                  >
                    Остановить
                  </Button>
                )}
              </div>

              {(start.error || stop.error || runs.error || confirmDone.error) && (
                <Notice tone="error">
                  {(start.error || stop.error || runs.error || confirmDone.error).message}
                </Notice>
              )}
            </div>
          </>
        )}
      </div>

      {/* Live Execution Tiered View */}
      <AnimatePresence initial={false}>
        {liveActiveCard && (
          <motion.div
            key="arrodes-live"
            initial={reduced ? false : { opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduced ? { opacity: 0 } : { opacity: 0, y: -4 }}
            transition={reduced ? { duration: 0.01 } : PANEL_TRANSITION}
            className="relative overflow-hidden border border-[#543476] bg-[#131018] p-3 flex flex-col gap-2.5"
          >
            <LiveHorizon
              active={Boolean((arrodesRunning || liveRun?.status === 'running') && !liveRun?.pending_question)}
              tone={liveRun?.status === 'failed' ? 'danger' : (liveRun?.pending_question || liveRun?.browser_waiting) ? 'warning' : 'accent'}
            />
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <StatusIndicator tone={(liveRun?.pending_question || liveRun?.browser_waiting) ? 'warning' : 'active'} pulse={!liveRun?.pending_question} />
                <span className="text-[13px] font-semibold text-[#EAEAEA] truncate">
                  {selectedTask.title}
                </span>
                <Badge variant={(liveRun?.pending_question || liveRun?.browser_waiting) ? 'warning' : 'accent'}>
                  {liveRun?.pending_question ? 'Ждёт ответа' : liveRun?.browser_waiting ? 'Ждёт браузер' : liveRun?.model_waiting ? 'Думает' : 'В работе'}
                </Badge>
              </div>
              {liveHasTokens && (
                <span className="text-[11px] text-[#929292] tabular-nums">
                  {liveRun.token_usage.input_tokens ?? 0} / {liveRun.token_usage.output_tokens ?? 0} tok
                </span>
              )}
            </div>

            <StageSwap stageKey={stageKey}>
              <div className="border-l-2 border-[#7F49B4] pl-2.5 flex flex-col gap-1 text-[12px]">
                <div className={liveRun?.browser_waiting ? 'text-[#D4A24C] font-medium' : 'text-[#EAEAEA] font-medium'}>
                  {liveRun?.browser_waiting
                    ? (liveRun?.current_action ?? 'Ждёт освобождения браузера')
                    : liveRun?.model_waiting
                      ? 'Думает…'
                      : (liveRun?.current_action ?? 'Подготовка среды')}
                </div>
                {liveRun?.action_description && !liveRun?.model_waiting && (
                  <div className="text-[#929292]">{liveRun.action_description}</div>
                )}
                {liveRun?.agent_comment && (
                  <div className="text-[#D8C2EE] whitespace-pre-wrap break-words pt-1">
                    {liveRun.agent_comment}
                  </div>
                )}
              </div>
            </StageSwap>

            {liveRun?.pending_question && (
              <AgentQuestion question={liveRun.pending_question} agent={liveRun.agent || 'Arrodes'} />
            )}

            {significantEvents.length > 0 && (
              <div className="border-t border-[#262030] pt-2 flex flex-col gap-1">
                {significantEvents.map((ev) => (
                  <div key={ev.id} className="text-[12px] text-[#DCDCDC] flex gap-2">
                    <span className="text-[#787878] tabular-nums shrink-0">{new Date(ev.at).toLocaleTimeString()}</span>
                    <span className="break-words">{ev.message}</span>
                  </div>
                ))}
              </div>
            )}

            {browserActive && (
              <div className="pt-1">
                <MonocleBrowser key={liveRun.id} active />
              </div>
            )}

            {liveRun && (
              <div className="border-t border-[#262030] pt-1.5">
                <Button size="xs" variant="subtle" onClick={() => setLogOpen(!logOpen)} aria-expanded={logOpen}>
                  {logOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                  <span>Журнал ({liveRun.events?.length ?? 0})</span>
                </Button>
                <CollapseSection open={logOpen}>
                  <div className="pt-2">
                    <AgentLog run={liveRun} provider="arrodes" maxHeightClass="max-h-[240px]" />
                  </div>
                </CollapseSection>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Results & Owner Confirmation */}
      {selectedTask && activeResult && (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <SectionHeader title="Результат" />
            {selectedTask.status !== 'done' && !selectedTask.archived && activeResult.status === 'succeeded' && (
              <Button
                size="sm"
                variant="accent-subtle"
                leftIcon={<CheckCircle2 className="h-3.5 w-3.5" />}
                loading={confirmDone.isPending}
                onClick={() => confirmDone.mutate()}
              >
                Подтвердить выполнение
              </Button>
            )}
          </div>

          <RunResultCard
            run={activeResult}
            disabled={arrodesRunning || start.isPending}
            defaultFilesOpen={!compact}
            onContinue={(id) => {
              setResumeRunId(id);
              setInstruction('');
            }}
          />

          {completedRuns.length > 1 && (
            <div className="flex flex-col gap-1.5">
              <div className="text-[12px] font-medium text-[#929292]">Запуски ({completedRuns.length})</div>
              <div className="md:hidden border border-[#303030] bg-[#111111] divide-y divide-[#222222]">
                {completedRuns.map((run) => {
                  const st = runStatusMeta[run.status] ?? { label: run.status, variant: 'default' };
                  const isCurrent = activeResult.id === run.id;
                  return (
                    <div
                      key={run.id}
                      onClick={() => setViewedRunId(run.id)}
                      className={`p-2.5 flex flex-col gap-1.5 cursor-pointer ${isCurrent ? 'bg-[#1A1622]' : ''}`}
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <Badge variant={st.variant}>{st.label}</Badge>
                        <span className="text-[11px] text-[#929292] tabular-nums">{new Date(run.created_at).toLocaleString()}</span>
                      </div>
                      <div className="text-[12px] text-[#B8B8B8] line-clamp-2 break-words">
                        {run.summary || run.error || '—'}
                      </div>
                      <div className="flex justify-end" onClick={(e) => e.stopPropagation()}>
                        <Button
                          size="xs"
                          variant="outline"
                          disabled={arrodesRunning || start.isPending}
                          onClick={() => {
                            setResumeRunId(run.id);
                            setInstruction('');
                          }}
                        >
                          Продолжить
                        </Button>
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="hidden md:block">
                <TableContainer>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Дата</TableHead>
                        <TableHead>Статус</TableHead>
                        <TableHead>Итог</TableHead>
                        <TableHead className="text-right" />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {completedRuns.map((run) => {
                        const st = runStatusMeta[run.status] ?? { label: run.status, variant: 'default' };
                        const isCurrent = activeResult.id === run.id;
                        return (
                          <TableRow
                            key={run.id}
                            className={`cursor-pointer ${isCurrent ? 'bg-[#1A1622]' : ''}`}
                            onClick={() => setViewedRunId(run.id)}
                          >
                            <TableCell className="text-[12px] tabular-nums whitespace-nowrap">
                              {new Date(run.created_at).toLocaleString()}
                            </TableCell>
                            <TableCell>
                              <Badge variant={st.variant}>{st.label}</Badge>
                            </TableCell>
                            <TableCell className="text-[12px] text-[#B8B8B8] max-w-[320px] truncate">
                              {run.summary || run.error || '—'}
                            </TableCell>
                            <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
                              <Button
                                size="xs"
                                variant="ghost"
                                disabled={arrodesRunning || start.isPending}
                                onClick={() => {
                                  setResumeRunId(run.id);
                                  setInstruction('');
                                }}
                              >
                                Продолжить
                              </Button>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </TableContainer>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
