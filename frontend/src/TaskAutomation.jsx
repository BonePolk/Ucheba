import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  Bot,
  CalendarPlus,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  ExternalLink,
  MoreHorizontal,
  Pencil,
  RefreshCw,
  Trash2,
} from 'lucide-react';
import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc';
import { agentRequest, deleteTask, request, saveTask } from './api';
import AgentProgress from './AgentProgress';
import AgentProblemReport from './AgentProblemReport';
import ArrodesWorkspace from './ArrodesWorkspace';
import ContextBrowser from './ContextBrowser';
import ContextFiles from './ContextFiles';
import RankingAssessment, { DeadlineRisk } from './RankingAssessment';
import TaskForm, { priorities, statuses } from './TaskForm';
import TaskMarkdown from './TaskMarkdown';
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
import { Badge, Notice, SectionHeader } from '@/components/ui/surface';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { CollapseSection, ExpandableText, LiveHorizon, TabTransition } from '@/lib/motion';

dayjs.extend(utc);

function formatCompactDeadline(iso) {
  if (!iso) return 'Без срока';
  const dt = dayjs.utc(iso).local();
  return dt.year() === dayjs().year() ? dt.format('DD MMM, HH:mm') : dt.format('DD.MM.YY HH:mm');
}

function ContextForm({ taskId, context }) {
  const client = useQueryClient();
  const [answer, setAnswer] = useState(null);

  const analyze = useMutation({
    mutationFn: () => agentRequest(`/integrations/tasks/${taskId}/monocle/analyze`, { method: 'POST' }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['context', taskId] });
      await client.invalidateQueries({ queryKey: ['tasks'] });
      await client.invalidateQueries({ queryKey: ['ameba-ranking'] });
    },
    onSettled: () => client.invalidateQueries({ queryKey: ['context-files', taskId] }),
  });

  const save = useMutation({
    mutationFn: () => agentRequest(`/integrations/tasks/${taskId}/monocle`, {
      method: 'POST',
      body: JSON.stringify({ subject: context.data?.subject ?? '', answer: answer ?? context.data?.owner_notes ?? '' }),
    }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['context', taskId] });
      await client.invalidateQueries({ queryKey: ['tasks'] });
      await client.invalidateQueries({ queryKey: ['ameba-ranking'] });
    },
    onSettled: () => client.invalidateQueries({ queryKey: ['context-files', taskId] }),
  });

  const busy = analyze.isPending || save.isPending;
  const usage = context.data?.usage;
  const hasTokens = usage && (usage.input_tokens != null || usage.output_tokens != null);

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-[12px] text-[#929292]">Заметки и контекст</span>
        {hasTokens && (
          <span className="text-[11px] text-[#929292] tabular-nums">
            {usage.input_tokens ?? 0} / {usage.output_tokens ?? 0} tok
          </span>
        )}
      </div>

      <Textarea
        aria-label="Поручение или ответ для Monocle"
        placeholder="Поручение, ссылки или ответы на вопросы…"
        rows={2}
        maxLength={12000}
        value={answer ?? context.data?.owner_notes ?? ''}
        onChange={(event) => setAnswer(event.currentTarget.value)}
      />

      <div className="flex flex-wrap items-center gap-1.5">
        <Button
          size="sm"
          variant="primary"
          loading={save.isPending}
          disabled={analyze.isPending || !(answer ?? context.data?.owner_notes ?? '').trim()}
          onClick={() => save.mutate()}
        >
          Отправить
        </Button>
        <Button
          size="sm"
          variant="default"
          loading={analyze.isPending}
          disabled={save.isPending}
          onClick={() => analyze.mutate()}
        >
          Проверить
        </Button>
      </div>

      {analyze.isSuccess && (
        <Notice tone={analyze.data.status === 'ready' ? 'success' : 'warning'}>
          {analyze.data.status === 'ready'
            ? 'Контекст собран.'
            : 'Нужны уточнения — см. вопросы.'}
        </Notice>
      )}
      {usage?.updated_fields?.length > 0 && (
        <div className="text-[12px] text-[#929292]">
          Обновлено: {usage.updated_fields.map((name) => ({ title: 'название', description: 'описание', category: 'категория' })[name]).join(', ')}.
        </div>
      )}
      {save.data?.metrics?.status === 'needs_context' && (
        <Notice tone="warning">Нужны дополнительные уточнения.</Notice>
      )}
      {save.data?.rank != null && (
        <div className="text-[12px] text-[#CFA9F5]">
          Ameba: #{save.data.rank} из {save.data.ranked_count}
        </div>
      )}
      {save.data?.reasons && (
        <RankingAssessment assessment={save.data.reasons.find((item) => item.task_id === taskId)} />
      )}
      {(save.error || analyze.error || context.error) && (
        <Notice tone="error">
          {(save.error || analyze.error || context.error).message}
          <AgentProblemReport agent="Monocle" toolName="context_analysis" />
        </Notice>
      )}

      <div className="pt-1.5 border-t border-[#222222]">
        <ContextBrowser taskId={taskId} disabled={busy} />
      </div>
    </div>
  );
}

export default function TaskAutomation({ task, features = {} }) {
  const client = useQueryClient();
  const [notice, setNotice] = useState('');
  const [editing, setEditing] = useState(false);
  const [customMinutes, setCustomMinutes] = useState('');
  const [minutesEditorOpen, setMinutesEditorOpen] = useState(false);
  const [markdownOpen, setMarkdownOpen] = useState(false);

  const requestedTab = new URLSearchParams(window.location.search).get('tab');
  const [userAgentTab, setUserAgentTab] = useState(() => (requestedTab === 'arrodes' || requestedTab === 'monocle' ? requestedTab : null));

  const queue = useQuery({
    queryKey: ['agent-queue'],
    queryFn: () => request('/integrations/queue'),
    enabled: features.ameba !== false,
    refetchInterval: 1000,
  });
  const context = useQuery({
    queryKey: ['context', task.id],
    queryFn: () => request(`/integrations/tasks/${task.id}/monocle`),
    refetchInterval: 5000,
    enabled: features.monocle !== false,
  });
  const ranking = useQuery({
    queryKey: ['ameba-ranking'],
    queryFn: () => request('/integrations/ameba/ranking'),
    enabled: features.ameba !== false && !task.archived,
    refetchInterval: 15000,
  });

  const invalidateAll = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['tasks'] }),
      client.invalidateQueries({ queryKey: ['tasks', String(task.id)] }),
      client.invalidateQueries({ queryKey: ['ameba-ranking'] }),
      client.invalidateQueries({ queryKey: ['context', task.id] }),
    ]);
  };

  const updateTask = useMutation({
    mutationFn: (data) => saveTask({ id: task.id, data }),
    onSuccess: async () => {
      setEditing(false);
      setMinutesEditorOpen(false);
      await invalidateAll();
    },
  });

  const removeTask = useMutation({
    mutationFn: (permanent) => deleteTask({ id: task.id, permanent }),
    onSuccess: async () => {
      await invalidateAll();
      window.history.pushState(null, '', '/');
      window.dispatchEvent(new CustomEvent('ucheba:navigate'));
    },
  });

  const rankAction = useMutation({
    mutationFn: () => agentRequest(
      queue.data?.ameba_running ? '/integrations/ameba/rank' : `/integrations/tasks/${task.id}/ameba`,
      { method: 'POST' },
    ),
    onSuccess: async (data) => {
      setNotice(
        data.unchanged
          ? (data.status === 'running' ? 'Ameba уже работает с этими данными.' : 'Данные не изменились.')
          : data.rank != null
            ? `Ameba: #${data.rank} из ${data.ranked_count}`
            : 'План Ameba обновлён',
      );
      await invalidateAll();
    },
  });

  const amebaPos = (ranking.data?.task_ids ?? []).indexOf(task.id);
  const rankNumber = amebaPos >= 0 ? amebaPos + 1 : null;
  const assessment = (ranking.data?.reasons ?? []).find((r) => r.task_id === task.id) ?? null;
  const scheduledPeriods = (ranking.data?.schedule?.periods ?? []).filter((p) => p.tasks?.some((t) => t.task_id === task.id));
  const overdue = !task.archived && task.status !== 'done' && task.deadline && dayjs.utc(task.deadline).valueOf() < Date.now();
  const summaryText = context.data?.full_markdown || context.data?.summary || '';
  const hasSeparateSummary = Boolean(
    features.monocle !== false
    && summaryText
    && task.description
    && summaryText.trim() !== task.description.trim(),
  );

  const arrodesEnabled = features.arrodes !== false;
  const monocleEnabled = features.monocle !== false;
  const agentTab = userAgentTab ?? (monocleEnabled && context.data?.questions?.length > 0 ? 'monocle' : arrodesEnabled ? 'arrodes' : 'monocle');

  return (
    <div className="flex flex-col gap-3">
      {/* Top breadcrumb + title */}
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2 text-[12px]">
            <a href="/" className="inline-flex items-center gap-1 text-[#929292] hover:text-[#EAEAEA]">
              <ArrowLeft className="h-3.5 w-3.5" />
              <span>Задачи</span>
            </a>
            {task.category && <Badge variant="outline">{task.category}</Badge>}
            {rankNumber != null && <Badge variant="accent">Ameba #{rankNumber}</Badge>}
            {task.archived && <Badge variant="outline">Архив</Badge>}
            {overdue && <Badge variant="danger">Просрочено</Badge>}
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <Button size="sm" variant="default" leftIcon={<Pencil className="h-3.5 w-3.5" />} onClick={() => setEditing(true)}>
              Изменить
            </Button>
            {!task.archived && (
              <Button size="sm" variant="default" leftIcon={<CalendarPlus className="h-3.5 w-3.5" />} asChild>
                <a href={`/calendar?add_task=${task.id}`}>В календарь</a>
              </Button>
            )}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <IconButton size="icon-sm" variant="default" aria-label="Дополнительные действия">
                  <MoreHorizontal className="h-3.5 w-3.5" />
                </IconButton>
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                {arrodesEnabled && !task.archived && (
                  <DropdownMenuItem asChild>
                    <a href={`/agents?tab=arrodes&task=${task.id}`}>
                      <Bot className="h-3.5 w-3.5" />
                      Arrodes (полный экран)
                    </a>
                  </DropdownMenuItem>
                )}
                {features.ameba !== false && !task.archived && (
                  <DropdownMenuItem
                    disabled={(rankAction.isPending && !queue.data?.ameba_running) || queue.data?.browser_busy}
                    onClick={() => rankAction.mutate()}
                  >
                    <RefreshCw className="h-3.5 w-3.5" />
                    Обновить Ameba
                  </DropdownMenuItem>
                )}
                {task.source_url && (
                  <DropdownMenuItem asChild>
                    <a href={task.source_url} target="_blank" rel="noreferrer">
                      <ExternalLink className="h-3.5 w-3.5" />
                      Telegram
                    </a>
                  </DropdownMenuItem>
                )}
                <DropdownMenuSeparator />
                {task.archived ? (
                  <>
                    <DropdownMenuItem onClick={() => updateTask.mutate({ archived: false })}>
                      Восстановить
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      danger
                      onClick={() => {
                        if (window.confirm(`Удалить задачу «${task.title}» навсегда?`)) removeTask.mutate(true);
                      }}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      Удалить навсегда
                    </DropdownMenuItem>
                  </>
                ) : (
                  <DropdownMenuItem danger onClick={() => removeTask.mutate(false)}>
                    <Trash2 className="h-3.5 w-3.5" />
                    В архив
                  </DropdownMenuItem>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>

        <div className="flex items-start gap-2 min-w-0">
          <h1 className={`text-[20px] font-semibold leading-tight m-0 break-words ${task.status === 'done' ? 'line-through text-[#929292]' : 'text-[#EAEAEA]'}`}>
            {task.title}
          </h1>
          <DeadlineRisk reason={assessment?.deadline_risk} />
        </div>
      </div>

      {/* Flat inline metadata strip */}
      <div className="border-y border-[#222222] py-1.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[12px]">
        <div className="flex items-center gap-1">
          <span className="text-[#929292]">Статус:</span>
          <SelectField
            size="sm"
            variant="ghost"
            className="w-[132px]"
            ariaLabel="Статус задачи"
            value={task.status}
            options={statuses}
            disabled={updateTask.isPending || task.archived}
            onChange={(status) => { if (status) updateTask.mutate({ status }); }}
          />
        </div>

        <div className="flex items-center gap-1">
          <span className="text-[#929292]">Приоритет:</span>
          <SelectField
            size="sm"
            variant="ghost"
            className="w-[108px]"
            ariaLabel="Приоритет задачи"
            value={task.priority}
            options={priorities}
            disabled={updateTask.isPending || task.archived}
            onChange={(priority) => { if (priority) updateTask.mutate({ priority }); }}
          />
        </div>

        <div className="flex items-center gap-1.5 tabular-nums">
          <span className="text-[#929292]">Срок:</span>
          <span className={overdue ? 'text-[#D46A6A] font-medium' : 'text-[#EAEAEA]'}>
            {formatCompactDeadline(task.deadline)}
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-1.5 ml-auto">
          <Clock className="h-3.5 w-3.5 text-[#929292]" aria-hidden="true" />
          <strong className="tabular-nums text-[#EAEAEA]">{task.completed_work_minutes || 0} мин</strong>
          {assessment?.plan?.hours_label && (
            <span className="text-[#929292]">· ост. {assessment.plan.hours_label}</span>
          )}
          {!task.archived && (
            <>
              <Button size="xs" variant="outline" disabled={updateTask.isPending} onClick={() => updateTask.mutate({ completed_work_minutes: (task.completed_work_minutes || 0) + 15 })}>+15м</Button>
              <Button size="xs" variant="outline" disabled={updateTask.isPending} onClick={() => updateTask.mutate({ completed_work_minutes: (task.completed_work_minutes || 0) + 30 })}>+30м</Button>
              <Button
                size="xs"
                variant="subtle"
                onClick={() => {
                  setCustomMinutes(String(task.completed_work_minutes || 0));
                  setMinutesEditorOpen(!minutesEditorOpen);
                }}
              >
                Время
              </Button>
            </>
          )}
          {task.status !== 'done' && !task.archived && (
            <Button
              size="xs"
              variant="accent-subtle"
              leftIcon={<CheckCircle2 className="h-3 w-3" />}
              disabled={updateTask.isPending}
              onClick={() => updateTask.mutate({ status: 'done' })}
            >
              Готово
            </Button>
          )}
        </div>
      </div>

      <CollapseSection open={minutesEditorOpen}>
        <div className="border border-[#303030] bg-[#141414] px-3 py-2 flex flex-wrap items-center gap-2 text-[12px]">
          <span className="text-[#929292]">Выполнено (мин):</span>
          <Input
            type="number"
            min={0}
            className="w-[100px] h-[26px]"
            value={customMinutes}
            onChange={(event) => setCustomMinutes(event.currentTarget.value)}
          />
          <Button
            size="xs"
            variant="primary"
            loading={updateTask.isPending}
            onClick={() => updateTask.mutate({ completed_work_minutes: Math.max(0, Number(customMinutes) || 0) })}
          >
            Сохранить
          </Button>
          <Button size="xs" variant="ghost" onClick={() => setMinutesEditorOpen(false)}>Отмена</Button>
        </div>
      </CollapseSection>

      {notice && <Notice tone="accent" onClose={() => setNotice('')}>{notice}</Notice>}
      {(updateTask.error || removeTask.error || rankAction.error) && (
        <Notice tone="error">{(updateTask.error || removeTask.error || rankAction.error).message}</Notice>
      )}

      {/* Main Two-Column Task Workspace */}
      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_420px] gap-5 items-start">
        {/* Left Column: Clean Flat Substance Sections */}
        <div className="flex flex-col gap-4 min-w-0">
          {monocleEnabled && context.data?.questions?.length > 0 && (
            <Notice tone="warning" title="Вопросы Monocle">
              <ul className="list-disc pl-4 m-0 space-y-0.5">
                {context.data.questions.map((question, i) => (
                  <li key={i}>{question}</li>
                ))}
              </ul>
            </Notice>
          )}

          {/* Description (no automatic double display when task.description already exists) */}
          <section className="flex flex-col gap-2 border-b border-[#222222] pb-3.5">
            <div className="flex items-center justify-between gap-2">
              <SectionHeader title="Описание" />
              {monocleEnabled && context.data?.status === 'new' && summaryText && (
                <Badge variant="warning">Устарел</Badge>
              )}
            </div>

            {task.description ? (
              <ExpandableText text={task.description} threshold={500} collapsedHeight="max-h-[180px]">
                <div className="text-[13px] text-[#EAEAEA] whitespace-pre-wrap break-words">{task.description}</div>
              </ExpandableText>
            ) : monocleEnabled && summaryText ? (
              <ExpandableText text={summaryText} threshold={460} collapsedHeight="max-h-[200px]">
                <TaskMarkdown>{summaryText}</TaskMarkdown>
              </ExpandableText>
            ) : (
              <div className="text-[12px] text-[#787878]">Без описания</div>
            )}

            {hasSeparateSummary && (
              <div className="pt-1 flex flex-col gap-1.5">
                <button
                  type="button"
                  onClick={() => setMarkdownOpen(!markdownOpen)}
                  aria-expanded={markdownOpen}
                  className="inline-flex items-center gap-1 text-[12px] text-[#B98CE8] hover:text-[#DFC4F8] cursor-pointer w-fit"
                >
                  {markdownOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                  <span>Разбор Monocle</span>
                </button>
                <CollapseSection open={markdownOpen}>
                  <div className="border-l-2 border-[#303030] pl-3 pt-1 flex flex-col gap-2">
                    <ExpandableText text={summaryText} threshold={600} collapsedHeight="max-h-[220px]">
                      <TaskMarkdown>{summaryText}</TaskMarkdown>
                    </ExpandableText>
                  </div>
                </CollapseSection>
              </div>
            )}

            {monocleEnabled && context.data?.usage?.sources?.length > 0 && (
              <div className="flex flex-wrap items-center gap-2 text-[12px] pt-1">
                <span className="text-[11px] text-[#929292]">Источники:</span>
                {context.data.usage.sources.map((source) => (
                  <a
                    key={source.url}
                    href={source.url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-[#B98CE8] hover:underline"
                  >
                    <span>{source.title}</span>
                    <ExternalLink className="h-3 w-3" />
                  </a>
                ))}
              </div>
            )}
          </section>

          {/* Ameba Assessment & Planned Periods */}
          {features.ameba !== false && assessment && (
            <section className="flex flex-col gap-2 border-b border-[#222222] pb-3.5">
              <div className="flex items-center justify-between gap-2">
                <SectionHeader title="Ameba" />
                <a href="/ameba" className="text-[12px] text-[#CFA9F5] hover:underline">План →</a>
              </div>
              <RankingAssessment assessment={assessment} />
              {scheduledPeriods.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5 text-[12px]">
                  <span className="text-[#929292]">Периоды:</span>
                  {scheduledPeriods.map((p) => {
                    const entry = p.tasks.find((t) => t.task_id === task.id);
                    return (
                      <a key={p.id} href="/ameba" className="px-2 py-0.5 border border-[#303030] bg-[#171717] hover:border-[#7F49B4]">
                        {p.start_date === p.end_date ? p.start_date : `${p.start_date}..${p.end_date}`} · {entry?.hours_label}
                      </a>
                    );
                  })}
                </div>
              )}
            </section>
          )}

          {/* Attached Files */}
          {monocleEnabled && (
            <section className="flex flex-col gap-2">
              <SectionHeader title="Файлы" />
              <ContextFiles taskId={task.id} disabled={false} />
            </section>
          )}
        </div>

        {/* Right Column: Single Tabbed Agent Panel + Unified Progress */}
        <aside className="flex flex-col gap-3 min-w-0">
          {(arrodesEnabled || monocleEnabled) && (
            <div className="relative overflow-hidden border border-[#303030] bg-[#111111] p-3">
              <LiveHorizon active={Boolean(queue.data?.busy)} />
              {arrodesEnabled && monocleEnabled ? (
                <Tabs value={agentTab} onValueChange={setUserAgentTab}>
                  <TabsList className="mb-3">
                    <TabsTrigger value="arrodes">Arrodes</TabsTrigger>
                    <TabsTrigger value="monocle">
                      Monocle
                      {context.data?.questions?.length > 0 && (
                        <Badge variant="warning" className="h-4 px-1 text-[10px]">{context.data.questions.length}</Badge>
                      )}
                    </TabsTrigger>
                  </TabsList>
                  <TabsContent value="arrodes" className="pt-0">
                    <TabTransition transitionKey="arrodes">
                      <ArrodesWorkspace task={task} features={features} compact />
                    </TabTransition>
                  </TabsContent>
                  <TabsContent value="monocle" className="pt-0">
                    <TabTransition transitionKey="monocle">
                      <ContextForm key={task.id} taskId={task.id} context={context} />
                    </TabTransition>
                  </TabsContent>
                </Tabs>
              ) : arrodesEnabled ? (
                <ArrodesWorkspace task={task} features={features} compact />
              ) : (
                <ContextForm key={task.id} taskId={task.id} context={context} />
              )}
            </div>
          )}

          <AgentProgress
            taskId={task.id}
            providers={['monocle', 'arrodes', 'ameba'].filter((name) => features[name] !== false)}
            active={rankAction.isPending}
            defaultOpen={false}
          />
        </aside>
      </div>

      {editing && (
        <TaskForm
          task={task}
          onClose={() => setEditing(false)}
          allowAutomation={false}
          allowContext={false}
          onSubmit={(data) => updateTask.mutate(data)}
          pending={updateTask.isPending}
          error={updateTask.error}
        />
      )}
    </div>
  );
}
