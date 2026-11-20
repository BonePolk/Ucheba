import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc';
import {
  ArrowUpRight,
  Bot,
  CalendarPlus,
  ExternalLink,
  Filter,
  MoreHorizontal,
  Pencil,
  Plus,
  Search,
  Trash2,
  X,
} from 'lucide-react';
import { agentRequest, deleteTask, getTasks, request, saveTask } from './api';
import { uploadTaskFiles } from './fileUpload';
import TaskForm, { priorities, statuses } from './TaskForm';
import TaskCard from './TaskCard';
import AgentQueue from './AgentQueue';
import AgentProgress from './AgentProgress';
import { AmebaSummaryBar } from './AmebaWorkspace';
import { DeadlineRisk } from './RankingAssessment';
import { cn, useMediaQuery } from '@/lib/utils';
import { AnimatePresence, CollapseSection, LAYOUT_TRANSITION, motion, useReducedMotion } from '@/lib/motion';
import { Button, IconButton } from '@/components/ui/button';
import { Checkbox, Progress } from '@/components/ui/controls';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Field, Input } from '@/components/ui/input';
import { Combobox, SelectField } from '@/components/ui/select';
import { Badge, EmptyState, Notice, PageHeader, PageLoader, StatusIndicator } from '@/components/ui/surface';
import { Table, TableBody, TableCell, TableContainer, TableHead, TableHeader, TableRow, tableRowClass } from '@/components/ui/table';
import { SegmentedControl } from '@/components/ui/tabs';
import { HintTooltip } from '@/components/ui/tooltip';

dayjs.extend(utc);

function isOverdue(task, now) {
  return !task.archived && task.status !== 'done' && task.deadline && dayjs.utc(task.deadline).valueOf() < now;
}

const sorting = [
  { value: 'ameba:asc', label: 'Ameba' },
  { value: 'deadline:asc', label: 'Срок ↑' },
  { value: 'deadline:desc', label: 'Срок ↓' },
  { value: 'priority:asc', label: 'Приоритет ↑' },
  { value: 'priority:desc', label: 'Приоритет ↓' },
  { value: 'created_at:desc', label: 'Новые' },
  { value: 'created_at:asc', label: 'Старые' },
];

const priorityIndicator = {
  high: { label: 'Высокий', tone: 'danger' },
  medium: { label: 'Средний', tone: 'warning' },
  low: { label: 'Низкий', tone: 'idle' },
};

function formatCompactDeadline(iso) {
  if (!iso) return null;
  const dt = dayjs.utc(iso).local();
  return dt.year() === dayjs().year() ? dt.format('DD MMM, HH:mm') : dt.format('DD.MM.YY HH:mm');
}

export default function TaskBoard({ features = {} }) {
  const queryClient = useQueryClient();
  const reduced = useReducedMotion();
  const compact = useMediaQuery('(max-width: 767px)', typeof window !== 'undefined' ? window.innerWidth < 768 : false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(timer);
  }, []);

  const [view, setView] = useState('active');
  const [completing, setCompleting] = useState(null);
  const archived = view === 'archive';
  const [filters, setFilters] = useState({ status: null, priority: null, category: null });
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState(features.ameba !== false ? 'ameba:asc' : 'deadline:asc');
  const [editor, setEditor] = useState(null);

  const automationEnabled = features.arrodes !== false || features.ameba !== false || features.monocle !== false;
  const currentSort = features.ameba === false && sort === 'ameba:asc' ? 'deadline:asc' : sort;
  const [sortBy, order] = currentSort.split(':');

  const tasks = useQuery({
    queryKey: ['tasks', filters, currentSort, archived],
    queryFn: () => getTasks({ ...filters, sort_by: sortBy, order, archived: String(archived) }),
    refetchInterval: 15000,
  });
  const categories = useQuery({
    queryKey: ['tasks', 'categories', archived],
    queryFn: () => request(`/tasks/categories?archived=${archived}`),
  });
  const refresh = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['tasks'] }),
    queryClient.invalidateQueries({ queryKey: ['ameba-ranking'] }),
    queryClient.invalidateQueries({ queryKey: ['agent-memory'] }),
    queryClient.invalidateQueries({ queryKey: ['calendar'] }),
  ]);

  const ranking = useQuery({
    queryKey: ['ameba-ranking'],
    queryFn: () => request('/integrations/ameba/ranking'),
    enabled: features.ameba !== false && !archived,
    refetchInterval: 15000,
  });
  const queue = useQuery({
    queryKey: ['agent-queue'],
    queryFn: () => request('/integrations/queue'),
    enabled: !archived && (features.monocle !== false || features.straus !== false || features.arrodes !== false || features.ameba !== false),
    refetchInterval: (query) => (query.state.data?.busy ? 1500 : 5000),
  });
  const livePlan = useQuery({
    queryKey: ['ameba-live-plan'],
    queryFn: async () => null,
    enabled: false,
    initialData: null,
  });

  const rank = useMutation({
    mutationFn: () => agentRequest('/integrations/ameba/rank', {
      method: 'POST',
      body: JSON.stringify({}),
    }),
    onSuccess: async () => {
      setSort('ameba:asc');
      await refresh();
    },
  });

  const save = useMutation({
    mutationFn: async (variables) => {
      const task = await saveTask(variables);
      try {
        await uploadTaskFiles(task.id, variables.files ?? []);
      } catch (error) {
        error.savedTask = task;
        error.message = `Задача сохранена, но не все файлы прикреплены: ${error.message}`;
        throw error;
      }
      return task;
    },
    onError: (error) => {
      if (error.savedTask) refresh();
    },
    onSuccess: (task, variables) => {
      setEditor(null);
      if (variables.openAutomation) window.location.assign(`/?task=${task.id}`);
      return refresh();
    },
  });

  const update = useMutation({
    mutationFn: saveTask,
    onSuccess: async (task, { data }) => {
      if (data.status === 'done') {
        setCompleting(task.id);
        await new Promise((resolve) => setTimeout(resolve, 650));
      }
      await refresh();
      setCompleting(null);
    },
  });

  const remove = useMutation({ mutationFn: deleteTask, onSuccess: refresh });

  const rankingReasons = useMemo(
    () => new Map((ranking.data?.reasons ?? []).map((item) => [item.task_id, item])),
    [ranking.data?.reasons],
  );
  const positions = useMemo(
    () => new Map((ranking.data?.task_ids ?? []).map((id, index) => [id, index + 1])),
    [ranking.data?.task_ids],
  );

  const rawRows = tasks.data ?? [];
  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return rawRows;
    return rawRows.filter((t) => `${t.title} ${t.description ?? ''} ${t.category ?? ''}`.toLowerCase().includes(q));
  }, [rawRows, search]);

  const error = tasks.error || update.error || remove.error || ranking.error || rank.error;
  const activeFilterCount = Object.values(filters).filter(Boolean).length;
  const hasActiveSearchOrFilters = activeFilterCount > 0 || Boolean(search.trim());
  const changeFilter = (name, value) => setFilters((current) => ({ ...current, [name]: value }));

  return (
    <div className="flex flex-col gap-3">
      <PageHeader
        title="Задачи"
        count={rows.length}
        actions={(
          <>
            {!archived && (features.monocle !== false || features.straus !== false || features.arrodes !== false || features.ameba !== false) && (
              <AgentQueue queue={queue.data} error={queue.error} compact />
            )}
            <SegmentedControl
              ariaLabel="Раздел задач"
              value={view}
              onChange={(value) => {
                setView(value);
                setFilters({ status: null, priority: null, category: null });
                setSearch('');
              }}
              options={[{ value: 'active', label: 'Активные' }, { value: 'archive', label: 'Архив' }]}
            />
            <Button
              variant="primary"
              leftIcon={<Plus className="h-3.5 w-3.5" />}
              onClick={() => {
                save.reset();
                setEditor({ task: null });
              }}
            >
              {compact ? 'Задача' : 'Новая задача'}
            </Button>
          </>
        )}
      />

      {/* Single-line Filter Toolbar */}
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Input
            aria-label="Поиск задач"
            placeholder="Поиск…"
            value={search}
            onChange={(event) => setSearch(event.currentTarget.value)}
            leftIcon={<Search className="h-3.5 w-3.5" />}
            rightSlot={search ? (
              <button
                type="button"
                aria-label="Очистить поиск"
                onClick={() => setSearch('')}
                className="h-5 w-5 grid place-items-center text-[#929292] hover:text-[#EAEAEA] cursor-pointer"
              >
                <X className="h-3 w-3" />
              </button>
            ) : null}
            className="flex-1 min-w-[160px] sm:max-w-[300px]"
          />

          {compact ? (
            <Button
              variant="default"
              leftIcon={<Filter className="h-3.5 w-3.5" />}
              onClick={() => setFiltersOpen(!filtersOpen)}
              aria-expanded={filtersOpen}
            >
              Фильтры{activeFilterCount > 0 ? ` (${activeFilterCount})` : ''}
            </Button>
          ) : (
            <>
              <SelectField
                ariaLabel="Статус"
                placeholder="Статус"
                clearable
                clearLabel="Все статусы"
                value={filters.status}
                options={statuses}
                onChange={(value) => changeFilter('status', value)}
                className="w-[140px]"
              />
              <SelectField
                ariaLabel="Приоритет"
                placeholder="Приоритет"
                clearable
                clearLabel="Все приоритеты"
                value={filters.priority}
                options={[{ value: 'overdue', label: 'Просрочено' }, ...priorities]}
                onChange={(value) => changeFilter('priority', value)}
                className="w-[145px]"
              />
              <Combobox
                ariaLabel="Категория"
                placeholder="Категория"
                searchPlaceholder="Категория…"
                clearable
                value={filters.category}
                options={categories.data ?? []}
                onChange={(value) => changeFilter('category', value)}
                className="w-[155px]"
              />
              <SelectField
                ariaLabel="Сортировка"
                value={currentSort}
                options={sorting.filter((item) => features.ameba !== false || item.value !== 'ameba:asc')}
                onChange={(value) => { if (value) setSort(value); }}
                className="w-[180px]"
              />
              {hasActiveSearchOrFilters && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setFilters({ status: null, priority: null, category: null });
                    setSearch('');
                  }}
                >
                  Сбросить
                </Button>
              )}
            </>
          )}
        </div>

        {compact && (
          <CollapseSection open={filtersOpen}>
            <div className="border border-[#303030] bg-[#111111] p-2.5 grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              <Field label="Статус">
                <SelectField
                  ariaLabel="Статус"
                  placeholder="Все статусы"
                  clearable
                  clearLabel="Все статусы"
                  value={filters.status}
                  options={statuses}
                  onChange={(value) => changeFilter('status', value)}
                />
              </Field>
              <Field label="Приоритет">
                <SelectField
                  ariaLabel="Приоритет"
                  placeholder="Все приоритеты"
                  clearable
                  clearLabel="Все приоритеты"
                  value={filters.priority}
                  options={[{ value: 'overdue', label: 'Просрочено' }, ...priorities]}
                  onChange={(value) => changeFilter('priority', value)}
                />
              </Field>
              <Field label="Категория">
                <Combobox
                  ariaLabel="Категория"
                  placeholder="Все категории"
                  searchPlaceholder="Категория…"
                  clearable
                  value={filters.category}
                  options={categories.data ?? []}
                  onChange={(value) => changeFilter('category', value)}
                />
              </Field>
              <Field label="Сортировка">
                <SelectField
                  ariaLabel="Сортировка"
                  value={currentSort}
                  options={sorting.filter((item) => features.ameba !== false || item.value !== 'ameba:asc')}
                  onChange={(value) => { if (value) setSort(value); }}
                />
              </Field>
              {hasActiveSearchOrFilters && (
                <div className="sm:col-span-2 pt-0.5">
                  <Button
                    size="sm"
                    variant="outline"
                    className="w-full"
                    onClick={() => {
                      setFilters({ status: null, priority: null, category: null });
                      setSearch('');
                    }}
                  >
                    Сбросить фильтры
                  </Button>
                </div>
              )}
            </div>
          </CollapseSection>
        )}
      </div>

      {/* Compact Ameba Summary Bar */}
      {features.ameba !== false && !archived && (
        <AmebaSummaryBar
          ranking={ranking}
          queue={queue}
          rank={rank}
          livePlan={livePlan}
          onRefresh={() => rank.mutate()}
        />
      )}

      {/* Compact Agent Progress (auto-hides when idle, expands when active) */}
      {!archived && (
        <AgentProgress
          providers={['monocle', 'straus', 'arrodes', 'ameba'].filter((name) => features[name] !== false)}
          active={rank.isPending}
          defaultOpen={false}
          hideSummaryWhenIdle
        />
      )}

      {error && <Notice tone="error">{error.message}</Notice>}

      {/* Task List */}
      {tasks.isPending ? (
        <PageLoader />
      ) : rows.length === 0 ? (
        <EmptyState
          title={archived ? 'Архив пуст' : hasActiveSearchOrFilters ? 'Ничего не найдено' : 'Задач нет'}
          action={hasActiveSearchOrFilters ? (
            <Button
              size="sm"
              variant="default"
              onClick={() => {
                setFilters({ status: null, priority: null, category: null });
                setSearch('');
              }}
            >
              Сбросить
            </Button>
          ) : !archived ? (
            <Button variant="primary" leftIcon={<Plus className="h-3.5 w-3.5" />} onClick={() => { save.reset(); setEditor({ task: null }); }}>
              Новая задача
            </Button>
          ) : undefined}
        />
      ) : compact ? (
        <div className="grid grid-cols-1 gap-2">
          <AnimatePresence initial={false}>
            {rows.map((task) => (
              <TaskCard
                key={task.id}
                task={task}
                archived={archived}
                completing={completing === task.id}
                busy={update.isPending}
                removing={remove.isPending}
                arrodesEnabled={features.arrodes !== false}
                position={features.ameba !== false ? positions.get(task.id) : null}
                assessment={features.ameba !== false ? rankingReasons.get(task.id) : null}
                overdue={isOverdue(task, now)}
                onUpdate={(data) => update.mutate({ id: task.id, data })}
                onEdit={() => { save.reset(); setEditor({ task }); }}
                onRemove={() => {
                  if (!archived || window.confirm(`Удалить задачу «${task.title}» навсегда?`)) {
                    remove.mutate({ id: task.id, permanent: archived });
                  }
                }}
              />
            ))}
          </AnimatePresence>
        </div>
      ) : (
        <TableContainer>
          <Table className="min-w-[840px]">
            <TableHeader>
              <TableRow>
                <TableHead className="w-16">{features.ameba !== false && !archived ? '#' : 'Готово'}</TableHead>
                <TableHead>Задача</TableHead>
                <TableHead className="w-32">Время</TableHead>
                <TableHead className="w-36">Срок</TableHead>
                <TableHead className="w-24">Приоритет</TableHead>
                <TableHead className="w-36">Статус</TableHead>
                <TableHead className="w-24 text-right" />
              </TableRow>
            </TableHeader>
            <TableBody>
              <AnimatePresence initial={false}>
                {rows.map((task) => {
                  const done = task.status === 'done' || completing === task.id;
                  const overdue = isOverdue(task, now);
                  const pos = features.ameba !== false ? positions.get(task.id) : null;
                  const assessment = features.ameba !== false ? rankingReasons.get(task.id) : null;
                  const plan = assessment?.plan;
                  const completedMin = task.completed_work_minutes || 0;
                  const remainingMin = plan?.planned_work_minutes ?? plan?.work_max_minutes ?? 0;
                  const totalMin = completedMin + remainingMin;
                  let aiState = null;
                  if (features.monocle !== false && task.ai_details) {
                    try { aiState = JSON.parse(task.ai_details); } catch { /* ignore */ }
                  }
                  const pMeta = priorityIndicator[task.priority] ?? priorityIndicator.medium;
                  const deadlineLabel = formatCompactDeadline(task.deadline);

                  return (
                    <motion.tr
                      key={task.id}
                      layout={reduced ? false : 'position'}
                      initial={reduced ? false : { opacity: 0, y: 4 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0 }}
                      transition={reduced ? { duration: 0.01 } : LAYOUT_TRANSITION}
                      className={cn(tableRowClass, completing === task.id && 'task-completing')}
                    >
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <Checkbox
                          aria-label={`Выполнено: ${task.title}`}
                          checked={done}
                          disabled={update.isPending || archived}
                          onCheckedChange={(checked) => update.mutate({ id: task.id, data: { status: checked ? 'done' : 'todo' } })}
                        />
                        {pos != null && (
                          <HintTooltip label={assessment?.reason ? `Порядок Ameba #${pos}: ${assessment.reason}` : `Порядок Ameba #${pos}`}>
                            <span className="text-[11px] font-semibold text-[#B98CE8] tabular-nums cursor-help">
                              #{String(pos).padStart(2, '0')}
                            </span>
                          </HintTooltip>
                        )}
                      </div>
                    </TableCell>

                    <TableCell className="max-w-[420px]">
                      <div className="flex flex-wrap items-center gap-1.5 min-w-0">
                        <a
                          href={`/?task=${task.id}`}
                          className={`font-medium break-words hover:text-[#CFA9F5] hover:underline ${done ? 'line-through text-[#929292]' : 'text-[#EAEAEA]'}`}
                        >
                          {task.title}
                        </a>
                        {!archived && !done && features.ameba !== false && <DeadlineRisk reason={assessment?.deadline_risk} />}
                        {task.category && (
                          <Badge variant="outline" className="max-w-[140px] truncate">{task.category}</Badge>
                        )}
                        {aiState?.status === 'needs_context' && (
                          <HintTooltip label={aiState.reason}>
                            <a href={`/?task=${task.id}`}>
                              <Badge variant="warning" className="cursor-pointer">Вопрос Monocle</Badge>
                            </a>
                          </HintTooltip>
                        )}
                        {aiState?.status === 'context_pending' && (
                          <span className="text-[11px] text-[#787878]">Monocle…</span>
                        )}
                        {task.source_url && (
                          <HintTooltip label="Открыть источник в Telegram">
                            <a href={task.source_url} target="_blank" rel="noreferrer" className="text-[#929292] hover:text-[#CFA9F5]">
                              <ExternalLink className="h-3 w-3" />
                            </a>
                          </HintTooltip>
                        )}
                      </div>
                    </TableCell>

                    <TableCell className="tabular-nums text-[12px]">
                      {completedMin > 0 || plan ? (
                        <HintTooltip label={assessment?.time_estimate || (completedMin > 0 ? `Подтверждено: ${completedMin} мин` : undefined)}>
                          <div className="flex flex-col gap-1 min-w-0 cursor-help">
                            {totalMin > 0 && <Progress value={completedMin} max={totalMin} />}
                            <span className="text-[11px] text-[#B8B8B8] truncate">
                              {completedMin > 0 ? `${completedMin}м` : ''}
                              {completedMin > 0 && plan?.hours_label ? ' · ' : ''}
                              {plan?.hours_label ?? ''}
                            </span>
                          </div>
                        </HintTooltip>
                      ) : (
                        <span className="text-[#666666]">—</span>
                      )}
                    </TableCell>

                    <TableCell className="whitespace-nowrap tabular-nums text-[12px]">
                      {deadlineLabel ? (
                        <span className={overdue ? 'text-[#D46A6A] font-medium' : 'text-[#D0D0D0]'}>
                          {deadlineLabel}
                        </span>
                      ) : (
                        <span className="text-[#666666]">—</span>
                      )}
                    </TableCell>

                    <TableCell>
                      <StatusIndicator tone={pMeta.tone} label={pMeta.label} />
                    </TableCell>

                    <TableCell>
                      <SelectField
                        size="sm"
                        variant="ghost"
                        ariaLabel={`Статус: ${task.title}`}
                        value={done ? 'done' : task.status}
                        options={statuses}
                        disabled={update.isPending || archived}
                        onChange={(value) => { if (value) update.mutate({ id: task.id, data: { status: value } }); }}
                      />
                    </TableCell>

                    <TableCell className="text-right">
                      <div className="inline-flex items-center justify-end gap-0.5">
                        {!archived && (
                          <HintTooltip label="Изменить">
                            <IconButton
                              size="icon-sm"
                              variant="ghost"
                              aria-label={`Изменить ${task.title}`}
                              onClick={() => {
                                save.reset();
                                setEditor({ task });
                              }}
                            >
                              <Pencil className="h-3.5 w-3.5" />
                            </IconButton>
                          </HintTooltip>
                        )}
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <IconButton size="icon-sm" variant="ghost" aria-label={`Меню действий: ${task.title}`}>
                              <MoreHorizontal className="h-3.5 w-3.5" />
                            </IconButton>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent>
                            <DropdownMenuItem asChild>
                              <a href={`/?task=${task.id}`}>
                                <ArrowUpRight className="h-3.5 w-3.5" />
                                Открыть задачу
                              </a>
                            </DropdownMenuItem>
                            {!archived && features.arrodes !== false && (
                              <DropdownMenuItem asChild>
                                <a href={`/?task=${task.id}&tab=arrodes`}>
                                  <Bot className="h-3.5 w-3.5" />
                                  Поручить Arrodes
                                </a>
                              </DropdownMenuItem>
                            )}
                            {!archived && (
                              <DropdownMenuItem asChild>
                                <a href={`/calendar?add_task=${task.id}`}>
                                  <CalendarPlus className="h-3.5 w-3.5" />
                                  В календарь
                                </a>
                              </DropdownMenuItem>
                            )}
                            {archived && (
                              <DropdownMenuItem
                                disabled={update.isPending}
                                onClick={() => update.mutate({ id: task.id, data: { archived: false } })}
                              >
                                Восстановить
                              </DropdownMenuItem>
                            )}
                            <DropdownMenuSeparator />
                            <DropdownMenuItem
                              danger
                              disabled={remove.isPending}
                              onClick={() => {
                                if (!archived || window.confirm(`Удалить задачу «${task.title}» навсегда? Восстановить её будет невозможно.`)) {
                                  remove.mutate({ id: task.id, permanent: archived });
                                }
                              }}
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                              {archived ? 'Удалить навсегда' : 'В архив'}
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </TableCell>
                    </motion.tr>
                  );
                })}
              </AnimatePresence>
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {editor && (
        <TaskForm
          task={editor.task}
          onClose={() => setEditor(null)}
          allowAutomation={automationEnabled}
          allowContext={features.monocle !== false}
          onSubmit={(data, options) => save.mutate({ id: editor.task?.id ?? options.taskId, data, ...options })}
          pending={save.isPending}
          error={save.error}
        />
      )}
    </div>
  );
}
