import { useState } from 'react';
import {
  CalendarDays,
  ChevronDown,
  ChevronRight,
  MoreHorizontal,
} from 'lucide-react';
import { Button, IconButton } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Badge, EmptyState } from '@/components/ui/surface';
import { CollapseSection, LAYOUT_TRANSITION, motion, useReducedMotion } from '@/lib/motion';

const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const WEEKDAYS = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];

const OUTCOMES = [
  { value: 'done', label: 'Отметить выполненной', optionalMinutes: true },
  { value: 'partial', label: 'Сделана часть…', optionalMinutes: false },
  { value: 'too_hard', label: 'Идёт тяжелее оценки…', optionalMinutes: false },
];

export function parseIsoDate(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d));
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
  if (startIso === endIso) return `${sWd} · ${sDay} ${MONTHS_SHORT[sMon]}`;
  if (sMon === eMon) return `${sWd}–${eWd} · ${sDay}–${eDay} ${MONTHS_SHORT[sMon]}`;
  return `${sWd}–${eWd} · ${sDay} ${MONTHS_SHORT[sMon]} – ${eDay} ${MONTHS_SHORT[eMon]}`;
}

export function formatShortDate(iso) {
  const d = parseIsoDate(iso);
  return `${d.getUTCDate()} ${MONTHS_SHORT[d.getUTCMonth()]}`;
}

export function durationLabel(minutes) {
  if (!minutes) return '0 мин';
  if (minutes < 60) return `${minutes} мин`;
  const h = Math.round((minutes / 60) * 10) / 10;
  return `${h} ч`;
}

export default function AgentTimeline({
  finished = true,
  schedule,
  calendarOnly = false,
  onFeedback,
  onAdjust,
  busy = false,
  taskPins = {},
  estimateOverrides = {},
  onRefresh,
}) {
  const reduced = useReducedMotion();
  const [expanded, setExpanded] = useState(false);
  const [openReasons, setOpenReasons] = useState({});
  const [feedbackDraft, setFeedbackDraft] = useState(null);
  const [customMinutes, setCustomMinutes] = useState('');
  const [estimateDraft, setEstimateDraft] = useState(null);
  const [estimateInput, setEstimateInput] = useState('');

  const periods = schedule?.periods ?? [];
  const todayIso = schedule?.generated_date || new Date().toISOString().slice(0, 10);

  if (calendarOnly || !periods.length) {
    return (
      <EmptyState
        icon={<CalendarDays className="h-5 w-5" />}
        title={finished ? 'План ещё не построен' : 'Ameba составляет план…'}
        description={finished ? 'Нажмите «Построить план», чтобы получить распределение задач по ближайшим дням.' : undefined}
        action={finished && onRefresh ? <Button variant="primary" onClick={onRefresh}>Построить план</Button> : undefined}
      />
    );
  }

  const visiblePeriods = expanded ? periods : periods.slice(0, 10);

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

  function openEstimateEditor(taskId, minutesValue, periodId) {
    setFeedbackDraft(null);
    setEstimateDraft({ taskId, periodId });
    setEstimateInput(minutesValue !== undefined ? String(minutesValue) : '');
  }

  function toggleReason(key) {
    setOpenReasons((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  function renderTaskMenu(taskId, periodId, defaultMinutes) {
    if (!onFeedback && !onAdjust) return null;
    const isPinned = Boolean(taskPins[String(taskId)]);
    const manualVal = estimateOverrides[String(taskId)];

    return (
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <IconButton
            size="icon-sm"
            variant="ghost"
            aria-label="Действия с задачей"
            disabled={busy}
          >
            <MoreHorizontal className="h-3.5 w-3.5" />
          </IconButton>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {onFeedback && OUTCOMES.map((item) => (
            <DropdownMenuItem
              key={item.value}
              onClick={() => {
                if (item.value === 'done') {
                  onFeedback({ task_id: taskId, period_id: periodId, outcome: 'done', actual_minutes: null });
                } else {
                  openFeedback(taskId, periodId, item.value);
                }
              }}
            >
              {item.label}
            </DropdownMenuItem>
          ))}
          {onAdjust && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => onAdjust({ task_pin: { task_id: taskId, shift_days: -1 } })}>
                Перенести раньше (−1 д)
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => onAdjust({ task_pin: { task_id: taskId, shift_days: 1 } })}>
                Перенести позже (+1 д)
              </DropdownMenuItem>
              {isPinned && (
                <DropdownMenuItem onClick={() => onAdjust({ task_pin: { task_id: taskId, clear: true } })}>
                  Снять перенос
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onClick={() => openEstimateEditor(taskId, manualVal ?? defaultMinutes, periodId)}>
                Изменить остаток времени…
              </DropdownMenuItem>
              {manualVal !== undefined && (
                <DropdownMenuItem onClick={() => onAdjust({ estimate_override: { task_id: taskId, remaining_minutes: null } })}>
                  Сбросить ручной остаток
                </DropdownMenuItem>
              )}
            </>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem asChild>
            <a href={`/?task=${taskId}`}>Открыть задачу</a>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    );
  }

  function renderInlineEditors(taskId, periodId) {
    const editingThis = feedbackDraft?.taskId === taskId && feedbackDraft?.periodId === periodId;
    const editingEstThis = estimateDraft?.taskId === taskId && estimateDraft?.periodId === periodId;
    const activeOutcome = editingThis ? OUTCOMES.find((o) => o.value === feedbackDraft.outcome) : null;

    return (
      <>
        <CollapseSection open={editingThis}>
          {editingThis && (
            <div className="mt-2 pt-2 border-t border-dashed border-[#303030] flex flex-wrap items-center gap-1.5">
              <span className="text-[12px] font-medium text-[#EAEAEA]">
                {activeOutcome?.label?.replace('…', '')}:
              </span>
              <Input
                type="number"
                min={0}
                max={1440}
                placeholder="Минуты"
                aria-label="Затрачено минут"
                value={customMinutes}
                onChange={(event) => setCustomMinutes(event.currentTarget.value)}
                className="w-[88px] max-md:w-[104px] h-[25px] text-[12px]"
              />
              <Button size="xs" variant="outline" onClick={() => submitDraft(30)}>30 мин</Button>
              <Button size="xs" variant="outline" onClick={() => submitDraft(60)}>1 ч</Button>
              <Button size="xs" variant="outline" onClick={() => submitDraft(120)}>2 ч</Button>
              <Button size="xs" variant="primary" loading={busy} onClick={() => submitDraft()}>Сохранить</Button>
              <Button size="xs" variant="ghost" onClick={() => setFeedbackDraft(null)}>✕</Button>
            </div>
          )}
        </CollapseSection>

        <CollapseSection open={editingEstThis}>
          {editingEstThis && (
            <div className="mt-2 pt-2 border-t border-dashed border-[#303030] flex flex-wrap items-center gap-1.5">
              <span className="text-[12px] text-[#929292]">Осталось минут:</span>
              <Input
                type="number"
                min={0}
                max={10000}
                placeholder="Минуты"
                aria-label="Осталось минут"
                value={estimateInput}
                onChange={(event) => setEstimateInput(event.currentTarget.value)}
                className="w-[90px] max-md:w-[104px] h-[25px] text-[12px]"
              />
              <Button
                size="xs"
                variant="primary"
                disabled={busy}
                onClick={() => {
                  const val = estimateInput.trim() === '' ? null : Math.max(0, Number(estimateInput));
                  onAdjust?.({ estimate_override: { task_id: taskId, remaining_minutes: val } });
                  setEstimateDraft(null);
                }}
              >
                Сохранить
              </Button>
              <Button size="xs" variant="ghost" onClick={() => setEstimateDraft(null)}>✕</Button>
            </div>
          )}
        </CollapseSection>
      </>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {visiblePeriods.map((period) => {
        const isCurrent = period.start_date <= todayIso && todayIso <= period.end_date;
        const dateLabel = formatDateRange(period.start_date, period.end_date);
        const isRest = period.kind === 'rest' || period.kind === 'free' || period.kind === 'recovery' || period.kind === 'light';

        if (isRest && !period.task_id && !(period.tasks?.length > 0)) {
          const reasonOpen = Boolean(openReasons[period.id]);
          return (
            <motion.article
              key={period.id}
              layout={reduced ? false : 'position'}
              transition={reduced ? { duration: 0.01 } : LAYOUT_TRANSITION}
              className="border border-[#242424] bg-[#0D0D0D] px-3 py-2 flex flex-col gap-1"
            >
              <div className="flex flex-wrap items-center justify-between gap-2 text-[12px]">
                <span className="text-[#929292]">{dateLabel}</span>
                {isCurrent && <Badge variant="outline">Сейчас</Badge>}
              </div>
              <div className="text-[13px] text-[#B8B8B8]">{period.goal || period.title}</div>
              {period.reason && (
                <div>
                  <button
                    type="button"
                    aria-expanded={reasonOpen}
                    onClick={() => toggleReason(period.id)}
                    className="inline-flex items-center gap-1 text-[11px] text-[#787878] hover:text-[#EAEAEA] cursor-pointer"
                  >
                    {reasonOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                    <span>Почему так</span>
                  </button>
                  <CollapseSection open={reasonOpen}>
                    <p className="mt-1 text-[12px] text-[#929292] leading-snug m-0">{period.reason}</p>
                  </CollapseSection>
                </div>
              )}
            </motion.article>
          );
        }

        // Новый формат v3: один период привязан к одной задаче period.task_id.
        if (period.task_id) {
          const reasonOpen = Boolean(openReasons[period.id]);
          const hasRisk = Boolean(period.deadline_risk);
          const isPinned = Boolean(taskPins[String(period.task_id)]);
          const manualVal = estimateOverrides[String(period.task_id)];

          return (
            <motion.article
              key={period.id}
              layout={reduced ? false : 'position'}
              transition={reduced ? { duration: 0.01 } : LAYOUT_TRANSITION}
              className="border border-[#303030] border-l-2 bg-[#111111] px-3 py-2.5 flex flex-col gap-1"
              style={{
                borderLeftColor: hasRisk ? '#D4A24C' : isCurrent ? '#7F49B4' : '#3A3A3A',
                background: isCurrent ? '#141118' : '#111111',
              }}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-1.5 min-w-0 text-[12px] text-[#929292]">
                  <span>{dateLabel}</span>
                  {isCurrent && <Badge variant="accent">Сейчас</Badge>}
                  {isPinned && <Badge variant="info">Закреплено</Badge>}
                  {manualVal !== undefined && <Badge variant="info">Ост. {manualVal} м</Badge>}
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  {period.hours_label && (
                    <span className="text-[12px] font-medium text-[#B8B8B8] tabular-nums">{period.hours_label}</span>
                  )}
                  {renderTaskMenu(period.task_id, period.id, period.minutes)}
                </div>
              </div>

              <a
                href={`/?task=${period.task_id}`}
                className="text-[14px] font-medium text-[#EAEAEA] hover:text-[#CFA9F5] hover:underline break-words w-fit"
              >
                {period.title}
              </a>

              {period.goal && (
                <p className="text-[12px] text-[#C8C8C8] leading-snug m-0 break-words">{period.goal}</p>
              )}

              {period.context_hint && (
                <p className="text-[12px] text-[#B98CE8] leading-snug m-0 break-words">{period.context_hint}</p>
              )}

              {period.reason && (
                <div className="pt-0.5">
                  <button
                    type="button"
                    aria-expanded={reasonOpen}
                    onClick={() => toggleReason(period.id)}
                    className="inline-flex items-center gap-1 text-[11px] text-[#8A8A8A] hover:text-[#EAEAEA] cursor-pointer"
                  >
                    {reasonOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                    <span>Почему так</span>
                  </button>
                  <CollapseSection open={reasonOpen}>
                    <p className="mt-1 text-[12px] text-[#929292] leading-snug m-0 break-words">{period.reason}</p>
                  </CollapseSection>
                </div>
              )}

              {renderInlineEditors(period.task_id, period.id)}
            </motion.article>
          );
        }

        // Совместимость со старым форматом v2 (несколько задач в одном периоде).
        return (
          <motion.article
            key={period.id}
            layout={reduced ? false : 'position'}
            transition={reduced ? { duration: 0.01 } : LAYOUT_TRANSITION}
            className="border border-[#303030] border-l-2 bg-[#111111] px-3 py-2.5 flex flex-col gap-2"
            style={{
              borderLeftColor: isCurrent ? '#7F49B4' : '#3A3A3A',
              background: isCurrent ? '#141118' : '#111111',
            }}
          >
            <div className="flex items-center justify-between gap-2 text-[12px] text-[#929292]">
              <div className="flex items-center gap-1.5">
                <span>{dateLabel}</span>
                {isCurrent && <Badge variant="accent">Сейчас</Badge>}
              </div>
              {period.hours_label && <span className="tabular-nums text-[#B8B8B8]">{period.hours_label}</span>}
            </div>
            <div className="flex flex-col divide-y divide-[#222222]">
              {(period.tasks ?? []).map((task) => (
                <div key={task.task_id} className="py-1.5 first:pt-0 last:pb-0">
                  <div className="flex items-center justify-between gap-2">
                    <a href={`/?task=${task.task_id}`} className="text-[13px] font-medium text-[#EAEAEA] hover:text-[#CFA9F5] hover:underline min-w-0 break-words">
                      {task.title}
                    </a>
                    <div className="flex items-center gap-1.5 shrink-0">
                      {task.hours_label && <span className="text-[12px] text-[#929292] tabular-nums">{task.hours_label}</span>}
                      {renderTaskMenu(task.task_id, period.id, task.minutes)}
                    </div>
                  </div>
                  {task.goal && <p className="text-[12px] text-[#929292] m-0 mt-0.5 break-words">{task.goal}</p>}
                  {renderInlineEditors(task.task_id, period.id)}
                </div>
              ))}
            </div>
          </motion.article>
        );
      })}

      {periods.length > 10 && (
        <div>
          <Button size="sm" variant="subtle" onClick={() => setExpanded(!expanded)}>
            {expanded ? 'Свернуть' : `Показать все (${periods.length})`}
          </Button>
        </div>
      )}
    </div>
  );
}
