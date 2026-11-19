import { useState } from 'react';
import { ArrowUpRight, Bot, CalendarPlus, ExternalLink, MoreHorizontal, Pencil, Trash2 } from 'lucide-react';
import dayjs from 'dayjs';
import { priorities, statuses } from './TaskForm';
import { DeadlineRisk } from './RankingAssessment';
import { IconButton } from '@/components/ui/button';
import { Checkbox, Progress } from '@/components/ui/controls';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { SelectField } from '@/components/ui/select';
import { Badge } from '@/components/ui/surface';
import { CollapseSection, LAYOUT_TRANSITION, motion, useReducedMotion } from '@/lib/motion';

const priorityVariant = { high: 'danger', medium: 'warning', low: 'default' };

export default function TaskCard({
  task,
  archived,
  completing,
  busy,
  removing,
  arrodesEnabled = false,
  position,
  assessment,
  onUpdate,
  onEdit,
  onRemove,
  overdue,
}) {
  const reduced = useReducedMotion();
  const [reasonOpen, setReasonOpen] = useState(false);
  let context = {};
  try {
    context = JSON.parse(task.ai_details || '{}');
  } catch { /* ignore */ }

  const done = task.status === 'done' || completing;
  const plan = assessment?.plan;
  const completedMin = task.completed_work_minutes || 0;
  const remainingMin = plan?.planned_work_minutes ?? plan?.work_max_minutes ?? 0;
  const totalMin = completedMin + remainingMin;

  return (
    <motion.article
      layout={reduced ? false : 'position'}
      initial={reduced ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={reduced ? { opacity: 0 } : { opacity: 0, y: -4 }}
      transition={reduced ? { duration: 0.01 } : LAYOUT_TRANSITION}
      className={`border border-[#303030] bg-[#111111] p-3 flex flex-col gap-2 transition-colors ${completing ? 'task-completing' : 'hover:border-[#454545]'}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5 min-w-0">
          {position != null && (
            assessment?.reason ? (
              <button
                type="button"
                onClick={() => setReasonOpen((v) => !v)}
                aria-expanded={reasonOpen}
                className="inline-flex cursor-pointer"
              >
                <Badge variant="accent" className="tabular-nums">
                  #{String(position).padStart(2, '0')}
                </Badge>
              </button>
            ) : (
              <Badge variant="accent" className="tabular-nums">
                #{String(position).padStart(2, '0')}
              </Badge>
            )
          )}
          <Badge variant={priorityVariant[task.priority] ?? 'default'}>
            {priorities.find((item) => item.value === task.priority)?.label}
          </Badge>
          {task.category && (
            <Badge variant="outline" className="max-w-[140px] truncate">
              {task.category}
            </Badge>
          )}
        </div>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <IconButton size="icon-sm" variant="ghost" aria-label={`Действия: ${task.title}`}>
              <MoreHorizontal className="h-3.5 w-3.5" />
            </IconButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent>
            <DropdownMenuItem asChild>
              <a href={`/?task=${task.id}`}>
                <ArrowUpRight className="h-3.5 w-3.5" />
                Открыть
              </a>
            </DropdownMenuItem>
            {!archived && arrodesEnabled && (
              <DropdownMenuItem asChild>
                <a href={`/?task=${task.id}&tab=arrodes`}>
                  <Bot className="h-3.5 w-3.5" />
                  Поручить Arrodes
                </a>
              </DropdownMenuItem>
            )}
            {!archived && (
              <DropdownMenuItem onClick={onEdit}>
                <Pencil className="h-3.5 w-3.5" />
                Изменить
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
              <DropdownMenuItem disabled={busy} onClick={() => onUpdate({ archived: false })}>
                Восстановить
              </DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem danger disabled={removing} onClick={onRemove}>
              <Trash2 className="h-3.5 w-3.5" />
              {archived ? 'Удалить' : 'В архив'}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {assessment?.reason && (
        <CollapseSection open={reasonOpen}>
          <div className="border-l-2 border-[#7F49B4] pl-2 py-0.5 text-[12px] text-[#CFCFCF] break-words">
            {assessment.reason}
          </div>
        </CollapseSection>
      )}

      <div className="flex items-start gap-3 min-w-0">
        <div className="pt-0.5 shrink-0">
          <Checkbox
            aria-label={`Выполнено: ${task.title}`}
            checked={done}
            disabled={busy || archived}
            onCheckedChange={(checked) => onUpdate({ status: checked ? 'done' : 'todo' })}
          />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-1 min-w-0">
            <a
              href={`/?task=${task.id}`}
              className={`block py-0.5 text-[14px] font-medium leading-snug break-words hover:text-[#CFA9F5] ${done ? 'line-through text-[#929292]' : 'text-[#EAEAEA]'}`}
            >
              {task.title}
            </a>
            {!done && !archived && <DeadlineRisk reason={assessment?.deadline_risk} />}
          </div>

          {(context.status === 'needs_context' || context.status === 'context_pending' || task.source_url) && (
            <div className="flex flex-wrap items-center gap-2 mt-1 text-[11px]">
              {context.status === 'needs_context' && (
                <a href={`/?task=${task.id}`} className="text-[#D4A24C] hover:underline">
                  Вопрос Monocle →
                </a>
              )}
              {context.status === 'context_pending' && (
                <span className="text-[#929292]">Monocle…</span>
              )}
              {task.source_url && (
                <a href={task.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-[#B98CE8] hover:underline">
                  Telegram <ExternalLink className="h-2.5 w-2.5" />
                </a>
              )}
            </div>
          )}
        </div>
      </div>

      {(completedMin > 0 || plan) && (
        <div className="flex items-center gap-2 text-[11px] text-[#929292] tabular-nums">
          {totalMin > 0 && <Progress value={completedMin} max={totalMin} className="flex-1" />}
          <span>
            {completedMin > 0 ? `${completedMin} мин` : ''}
            {completedMin > 0 && plan?.hours_label ? ' · ост. ' : ''}
            {plan?.hours_label ?? ''}
          </span>
        </div>
      )}

      <div className="flex items-center justify-between gap-2 pt-1.5 border-t border-[#202020]">
        <span className={`min-w-0 truncate text-[12px] tabular-nums ${overdue ? 'text-[#D46A6A] font-medium' : 'text-[#929292]'}`}>
          {task.deadline ? dayjs.utc(task.deadline).local().format('DD MMM · HH:mm') : 'Без срока'}
        </span>
        {!archived ? (
          <SelectField
            size="sm"
            className="w-[140px] shrink-0"
            ariaLabel={`Статус: ${task.title}`}
            value={done ? 'done' : task.status}
            options={statuses}
            disabled={busy}
            onChange={(status) => { if (status) onUpdate({ status }); }}
          />
        ) : (
          <Badge variant="outline">Архив</Badge>
        )}
      </div>
    </motion.article>
  );
}
