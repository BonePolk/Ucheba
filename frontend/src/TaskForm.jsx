import { useEffect, useRef, useState } from 'react';
import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc';
import { ChevronDown, ChevronRight, Paperclip, X } from 'lucide-react';
import { MAX_FILE_SIZE } from './fileUpload';
import { Button, IconButton } from '@/components/ui/button';
import { Checkbox, FileTrigger } from '@/components/ui/controls';
import { Modal } from '@/components/ui/dialog';
import { Field, Input, Textarea } from '@/components/ui/input';
import { SelectField } from '@/components/ui/select';
import { Notice } from '@/components/ui/surface';
import { cn } from '@/lib/utils';
import { AnimatePresence, CollapseSection, FAST_TRANSITION, motion, useReducedMotion } from '@/lib/motion';

dayjs.extend(utc);

export const priorities = [
  { value: 'low', label: 'Низкий' },
  { value: 'medium', label: 'Средний' },
  { value: 'high', label: 'Высокий' },
];

export const statuses = [
  { value: 'todo', label: 'К выполнению' },
  { value: 'in_progress', label: 'В процессе' },
  { value: 'review', label: 'На проверку' },
  { value: 'done', label: 'Выполнено' },
];

export default function TaskForm({
  task,
  onClose,
  onSubmit,
  pending,
  error,
  allowAutomation = false,
  allowContext = false,
}) {
  const isEdit = Boolean(task?.id);
  const reduced = useReducedMotion();
  const fileReset = useRef(null);
  const [title, setTitle] = useState(task?.title ?? '');
  const [description, setDescription] = useState(task?.description ?? '');
  const [deadline, setDeadline] = useState(
    task?.deadline ? dayjs.utc(task.deadline).local().format('YYYY-MM-DDTHH:mm') : '',
  );
  const [priority, setPriority] = useState(task?.priority ?? 'medium');
  const [status, setStatus] = useState(task?.status ?? 'todo');
  const [category, setCategory] = useState(task?.category ?? '');
  const [completedWorkMinutes, setCompletedWorkMinutes] = useState(String(task?.completed_work_minutes ?? 0));
  const [agentNotes, setAgentNotes] = useState('');
  const [openAutomation, setOpenAutomation] = useState(false);
  const [moreOpen, setMoreOpen] = useState(isEdit);
  const [titleError, setTitleError] = useState('');

  const [files, setFiles] = useState([]);
  const [fileError, setFileError] = useState('');
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (error?.remainingFiles) setFiles(error.remainingFiles);
  }, [error]);

  function addFiles(selected) {
    if (pending) return;
    if (files.length + selected.length > 5) {
      setFileError('Можно прикрепить до пяти файлов за раз');
      return;
    }
    if (selected.some((file) => file.size === 0)) {
      setFileError('Нельзя прикрепить пустой файл (0 байт)');
      return;
    }
    if (selected.some((file) => file.size > MAX_FILE_SIZE)) {
      setFileError('Максимальный размер файла — 25 МБ');
      return;
    }
    setFiles([...files, ...selected]);
    setFileError('');
  }

  function handleSubmit(event) {
    event.preventDefault();
    if (!title.trim()) {
      setTitleError('Укажите название');
      return;
    }
    setTitleError('');
    const parsedMinutes = Math.max(0, Math.floor(Number(completedWorkMinutes) || 0));
    const payload = {
      title: title.trim(),
      description: description.trim() || null,
      deadline: deadline ? dayjs(deadline).toISOString() : null,
      priority,
      status,
      category: category.trim() || null,
      completed_work_minutes: parsedMinutes,
      ...(!isEdit && allowContext ? { agent_notes: agentNotes } : {}),
    };
    onSubmit(payload, {
      openAutomation: allowAutomation && openAutomation,
      files,
      taskId: error?.savedTask?.id,
    });
  }

  function setQuickDeadline(daysOffset) {
    setDeadline(dayjs().add(daysOffset, 'day').hour(23).minute(59).format('YYYY-MM-DDTHH:mm'));
  }

  return (
    <Modal opened onClose={onClose} title={isEdit ? 'Редактировать задачу' : 'Новая задача'} size="md">
      <form className="flex flex-col gap-3" onSubmit={handleSubmit}>
        <Field label="Название" error={titleError} htmlFor="task-form-title">
          <Input
            id="task-form-title"
            placeholder="Что нужно сделать?"
            required
            autoFocus={!isEdit}
            value={title}
            error={Boolean(titleError)}
            onChange={(event) => {
              setTitle(event.currentTarget.value);
              if (titleError) setTitleError('');
            }}
          />
        </Field>

        <Field label="Описание" htmlFor="task-form-desc">
          <Textarea
            id="task-form-desc"
            placeholder="Коротко о задаче"
            rows={2}
            value={description}
            onChange={(event) => setDescription(event.currentTarget.value)}
          />
        </Field>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
          <Field
            label="Дедлайн"
            htmlFor="task-form-deadline"
            rightLabel={(
              <span className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setQuickDeadline(0)}
                  className="px-2 py-0.5 max-md:px-2.5 max-md:py-1 border border-[#303030] bg-[#161616] text-[11px] max-md:text-[12px] text-[#B8B8B8] hover:text-[#EAEAEA] hover:border-[#454545] cursor-pointer"
                >
                  Сегодня
                </button>
                <button
                  type="button"
                  onClick={() => setQuickDeadline(1)}
                  className="px-2 py-0.5 max-md:px-2.5 max-md:py-1 border border-[#303030] bg-[#161616] text-[11px] max-md:text-[12px] text-[#B8B8B8] hover:text-[#EAEAEA] hover:border-[#454545] cursor-pointer"
                >
                  Завтра
                </button>
              </span>
            )}
          >
            <div className="flex items-center gap-1">
              <Input
                id="task-form-deadline"
                type="datetime-local"
                value={deadline}
                onChange={(event) => setDeadline(event.currentTarget.value)}
              />
              {deadline && (
                <IconButton
                  size="icon"
                  variant="default"
                  aria-label="Очистить дедлайн"
                  onClick={() => setDeadline('')}
                >
                  <X className="h-3.5 w-3.5" />
                </IconButton>
              )}
            </div>
          </Field>

          <Field label="Приоритет" htmlFor="task-form-priority">
            <SelectField
              id="task-form-priority"
              value={priority}
              options={priorities}
              onChange={(val) => { if (val) setPriority(val); }}
            />
          </Field>
        </div>

        {allowContext && (
          <div
            className={cn(
              'border border-dashed border-[#303030] bg-[#0D0D0D] p-2 flex flex-col gap-1.5',
              dragging && 'file-drop-active',
            )}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              addFiles(Array.from(event.dataTransfer.files));
            }}
          >
            <FileTrigger multiple resetRef={fileReset} onChange={addFiles} disabled={pending}>
              {(props) => (
                <Button {...props} size="sm" variant="subtle" leftIcon={<Paperclip className="h-3.5 w-3.5" />} className="w-full justify-center">
                  Прикрепить файлы · до 5 × 25 МБ
                </Button>
              )}
            </FileTrigger>
            <AnimatePresence initial={false}>
              {files.map((file, index) => (
                <motion.div
                  key={`${file.name}:${file.size}:${index}`}
                  initial={reduced ? false : { opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={reduced ? { opacity: 0 } : { opacity: 0, height: 0 }}
                  transition={reduced ? { duration: 0.01 } : FAST_TRANSITION}
                  className="overflow-hidden"
                >
                  <div className="flex items-center justify-between gap-2 px-1.5 py-1 bg-[#141414] border border-[#242424] text-[12px]">
                    <span className="truncate">{file.name}</span>
                    <Button
                      size="xs"
                      variant="ghost"
                      disabled={pending}
                      onClick={() => setFiles(files.filter((_, number) => number !== index))}
                    >
                      Убрать
                    </Button>
                  </div>
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
        )}

        {fileError && <Notice tone="error">{fileError}</Notice>}

        <div className="border-t border-[#222222] pt-2">
          <button
            type="button"
            onClick={() => setMoreOpen(!moreOpen)}
            aria-expanded={moreOpen}
            className="inline-flex items-center gap-1 text-[12px] text-[#929292] hover:text-[#EAEAEA] cursor-pointer"
          >
            {moreOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
            <span>Дополнительно</span>
          </button>

          <CollapseSection open={moreOpen}>
            <div className="mt-2.5 flex flex-col gap-2.5">
              {!isEdit && allowContext && (
                <Field label="Заметки для Monocle" htmlFor="task-form-notes">
                  <Textarea
                    id="task-form-notes"
                    placeholder="Что проверить и учесть"
                    rows={2}
                    maxLength={12000}
                    value={agentNotes}
                    onChange={(event) => setAgentNotes(event.currentTarget.value)}
                  />
                </Field>
              )}

              {isEdit && (
                <Field label="Выполнено, мин" htmlFor="task-form-minutes">
                  <Input
                    id="task-form-minutes"
                    type="number"
                    min={0}
                    step={1}
                    value={completedWorkMinutes}
                    onChange={(event) => setCompletedWorkMinutes(event.currentTarget.value)}
                  />
                </Field>
              )}

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                <Field label="Статус" htmlFor="task-form-status">
                  <SelectField
                    id="task-form-status"
                    value={status}
                    options={statuses}
                    onChange={(val) => { if (val) setStatus(val); }}
                  />
                </Field>
                <Field label="Категория" htmlFor="task-form-category">
                  <Input
                    id="task-form-category"
                    placeholder="Учёба"
                    value={category}
                    onChange={(event) => setCategory(event.currentTarget.value)}
                  />
                </Field>
              </div>
            </div>
          </CollapseSection>
        </div>

        {allowAutomation && (
          <Checkbox
            label="Открыть задачу после сохранения"
            checked={openAutomation}
            onCheckedChange={setOpenAutomation}
          />
        )}

        {error && <Notice tone="error">{error.message}</Notice>}

        <div className="flex items-center justify-end gap-2 pt-2 border-t border-[#222222] max-sm:grid max-sm:grid-cols-2">
          <Button variant="default" onClick={onClose}>Отмена</Button>
          <Button type="submit" variant="primary" loading={pending}>Сохранить</Button>
        </div>
      </form>
    </Modal>
  );
}
