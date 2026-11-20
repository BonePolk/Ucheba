import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Check, ChevronDown, ChevronUp, Pencil, Plus, Trash2, X } from 'lucide-react';
import {
  createSubtask,
  deleteSubtask,
  reorderSubtasks,
  saveTask,
  updateSubtask,
} from './api';
import { Button, IconButton } from '@/components/ui/button';
import { Checkbox, Progress } from '@/components/ui/controls';
import { Input } from '@/components/ui/input';
import { Badge, Notice, SectionHeader } from '@/components/ui/surface';

function parseEstimatedMinutes(raw) {
  const trimmed = String(raw ?? '').trim();
  if (!trimmed) return null;
  const parsed = Math.round(Number(trimmed));
  if (!Number.isFinite(parsed) || parsed <= 0) return null;
  return Math.min(10080, parsed);
}

export default function TaskSubtasks({
  task,
  monocleEnabled = false,
  onChanged,
}) {
  const subtasks = task.subtasks ?? [];
  const total = subtasks.length;
  const doneCount = subtasks.filter((item) => item.done).length;
  const remainingMinutes = subtasks.reduce(
    (sum, item) => (!item.done && item.estimated_minutes > 0 ? sum + item.estimated_minutes : sum),
    0,
  );

  const [newTitle, setNewTitle] = useState('');
  const [newMinutes, setNewMinutes] = useState('');
  const [editingId, setEditingId] = useState(null);
  const [editTitle, setEditTitle] = useState('');
  const [editMinutes, setEditMinutes] = useState('');

  const create = useMutation({
    mutationFn: (data) => createSubtask({ taskId: task.id, data }),
    onSuccess: async () => {
      setNewTitle('');
      setNewMinutes('');
      await onChanged?.();
    },
  });

  const update = useMutation({
    mutationFn: ({ subtaskId, data }) => updateSubtask({ taskId: task.id, subtaskId, data }),
    onSuccess: async () => {
      setEditingId(null);
      await onChanged?.();
    },
  });

  const remove = useMutation({
    mutationFn: (subtaskId) => deleteSubtask({ taskId: task.id, subtaskId }),
    onSuccess: async () => {
      if (editingId != null) setEditingId(null);
      await onChanged?.();
    },
  });

  const reorder = useMutation({
    mutationFn: (subtaskIds) => reorderSubtasks({ taskId: task.id, subtaskIds }),
    onSuccess: async () => {
      await onChanged?.();
    },
  });

  const unlockMonocle = useMutation({
    mutationFn: () => saveTask({ id: task.id, data: { subtasks_locked: false } }),
    onSuccess: async () => {
      await onChanged?.();
    },
  });

  const busy = create.isPending
    || update.isPending
    || remove.isPending
    || reorder.isPending
    || unlockMonocle.isPending;

  const error = create.error
    || update.error
    || remove.error
    || reorder.error
    || unlockMonocle.error;

  function startEdit(item) {
    if (task.archived) return;
    setEditingId(item.id);
    setEditTitle(item.title);
    setEditMinutes(item.estimated_minutes != null ? String(item.estimated_minutes) : '');
  }

  function saveEdit(item) {
    const trimmed = editTitle.trim();
    if (!trimmed) return;
    update.mutate({
      subtaskId: item.id,
      data: {
        title: trimmed,
        estimated_minutes: parseEstimatedMinutes(editMinutes),
      },
    });
  }

  function moveItem(index, delta) {
    const target = index + delta;
    if (target < 0 || target >= subtasks.length) return;
    const ids = subtasks.map((item) => item.id);
    const [moved] = ids.splice(index, 1);
    ids.splice(target, 0, moved);
    reorder.mutate(ids);
  }

  const actions = (
    <div className="flex flex-wrap items-center gap-1.5">
      {remainingMinutes > 0 && (
        <span className="text-[11px] text-[#929292] tabular-nums">ост. ~{remainingMinutes} мин</span>
      )}
      {total === 0 && !task.archived && task.subtasks_locked && monocleEnabled && (
        <Button
          size="xs"
          variant="ghost"
          loading={unlockMonocle.isPending}
          disabled={busy}
          onClick={() => unlockMonocle.mutate()}
        >
          Разрешить Monocle
        </Button>
      )}
    </div>
  );

  return (
    <section className="flex flex-col gap-2 border-b border-[#222222] pb-3.5">
      <SectionHeader
        title="Подзадачи"
        meta={total > 0 ? `${doneCount}/${total}` : undefined}
        actions={actions}
      />

      {total > 0 && (
        <Progress value={doneCount} max={total} tone={doneCount === total ? 'success' : 'accent'} />
      )}

      {total > 0 ? (
        <div className="flex flex-col divide-y divide-[#1B1B1B] border border-[#242424] bg-[#0E0E0E]">
          {subtasks.map((item, index) => {
            const isEditing = editingId === item.id;
            const showSource = item.source === 'monocle' && !item.user_edited;

            if (isEditing) {
              return (
                <div
                  key={item.id}
                  className="flex flex-wrap sm:flex-nowrap items-center gap-1.5 px-2.5 py-2 bg-[#141414]"
                  onKeyDown={(event) => {
                    if (event.key === 'Escape') {
                      event.preventDefault();
                      setEditingId(null);
                    } else if (event.key === 'Enter') {
                      event.preventDefault();
                      saveEdit(item);
                    }
                  }}
                >
                  <Input
                    autoFocus
                    aria-label="Название подзадачи"
                    maxLength={300}
                    value={editTitle}
                    onChange={(event) => setEditTitle(event.currentTarget.value)}
                    className="flex-1 min-w-[160px] h-[28px]"
                  />
                  <Input
                    type="number"
                    min={0}
                    max={10080}
                    placeholder="мин"
                    aria-label="Оценка в минутах"
                    value={editMinutes}
                    onChange={(event) => setEditMinutes(event.currentTarget.value)}
                    className="w-[76px] h-[28px]"
                  />
                  <div className="flex items-center gap-1 shrink-0">
                    <IconButton
                      size="icon-sm"
                      variant="primary"
                      aria-label="Сохранить подзадачу"
                      disabled={busy || !editTitle.trim()}
                      onClick={() => saveEdit(item)}
                    >
                      <Check className="h-3.5 w-3.5" />
                    </IconButton>
                    <IconButton
                      size="icon-sm"
                      variant="ghost"
                      aria-label="Отменить редактирование"
                      disabled={update.isPending}
                      onClick={() => setEditingId(null)}
                    >
                      <X className="h-3.5 w-3.5" />
                    </IconButton>
                  </div>
                </div>
              );
            }

            return (
              <div
                key={item.id}
                className="flex items-start gap-2.5 px-2.5 py-2 hover:bg-[#131313] transition-colors"
              >
                <div className="pt-0.5 shrink-0">
                  <Checkbox
                    aria-label={`Подзадача: ${item.title}`}
                    checked={item.done}
                    disabled={busy || task.archived}
                    onCheckedChange={(checked) => update.mutate({ subtaskId: item.id, data: { done: checked } })}
                  />
                </div>

                <div className="flex-1 min-w-0 flex flex-wrap items-center gap-x-2 gap-y-1">
                  {task.archived ? (
                    <span className={`text-[13px] leading-snug break-words ${item.done ? 'line-through text-[#7D7D7D]' : 'text-[#EAEAEA]'}`}>
                      {item.title}
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => startEdit(item)}
                      className={`text-left text-[13px] leading-snug break-words cursor-pointer ${item.done ? 'line-through text-[#7D7D7D] hover:text-[#B8B8B8]' : 'text-[#EAEAEA] hover:text-[#CFA9F5]'}`}
                    >
                      {item.title}
                    </button>
                  )}
                  {item.estimated_minutes > 0 && (
                    <span className="text-[11px] text-[#929292] tabular-nums shrink-0">
                      ~{item.estimated_minutes} мин
                    </span>
                  )}
                  {showSource && (
                    <Badge variant="outline" className="h-4 px-1 text-[10px]">
                      Monocle
                    </Badge>
                  )}
                </div>

                {!task.archived && (
                  <div className="flex items-center gap-0.5 shrink-0">
                    <IconButton
                      size="icon-sm"
                      variant="ghost"
                      aria-label={`Выше: ${item.title}`}
                      disabled={busy || index === 0}
                      onClick={() => moveItem(index, -1)}
                    >
                      <ChevronUp className="h-3.5 w-3.5" />
                    </IconButton>
                    <IconButton
                      size="icon-sm"
                      variant="ghost"
                      aria-label={`Ниже: ${item.title}`}
                      disabled={busy || index === total - 1}
                      onClick={() => moveItem(index, 1)}
                    >
                      <ChevronDown className="h-3.5 w-3.5" />
                    </IconButton>
                    <IconButton
                      size="icon-sm"
                      variant="ghost"
                      aria-label={`Редактировать: ${item.title}`}
                      disabled={busy}
                      onClick={() => startEdit(item)}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </IconButton>
                    <IconButton
                      size="icon-sm"
                      variant="ghost"
                      aria-label={`Удалить: ${item.title}`}
                      disabled={busy}
                      className="hover:text-[#D46A6A]"
                      onClick={() => remove.mutate(item.id)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </IconButton>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="text-[12px] text-[#7D7D7D]">
          {task.subtasks_locked
            ? 'Автоподзадачи Monocle отключены после ручных правок.'
            : monocleEnabled
              ? 'Подзадачи пока не созданы — Monocle может добавить их автоматически для многошаговых задач, либо добавьте вручную.'
              : 'Подзадач пока нет.'}
        </div>
      )}

      {!task.archived && (
        <form
          className="flex flex-wrap sm:flex-nowrap items-center gap-1.5 pt-0.5"
          onSubmit={(event) => {
            event.preventDefault();
            const trimmed = newTitle.trim();
            if (!trimmed || busy) return;
            create.mutate({
              title: trimmed,
              estimated_minutes: parseEstimatedMinutes(newMinutes),
            });
          }}
        >
          <Input
            placeholder="Новая подзадача…"
            aria-label="Новая подзадача"
            maxLength={300}
            value={newTitle}
            onChange={(event) => setNewTitle(event.currentTarget.value)}
            className="flex-1 min-w-[160px]"
          />
          <Input
            type="number"
            min={0}
            max={10080}
            placeholder="мин"
            aria-label="Оценка в минутах"
            value={newMinutes}
            onChange={(event) => setNewMinutes(event.currentTarget.value)}
            className="w-[76px]"
          />
          <Button
            type="submit"
            size="sm"
            variant="default"
            leftIcon={<Plus className="h-3.5 w-3.5" />}
            loading={create.isPending}
            disabled={busy || !newTitle.trim()}
          >
            Добавить
          </Button>
        </form>
      )}

      {error && <Notice tone="error">{error.message}</Notice>}
    </section>
  );
}
