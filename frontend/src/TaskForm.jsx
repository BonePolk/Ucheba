import { Alert, Button, Checkbox, FileButton, Group, Modal, Select, SimpleGrid, Stack, Text, Textarea, TextInput } from '@mantine/core';
import { useEffect, useState } from 'react';
import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc';
import { DateTimePicker } from '@mantine/dates';
import { useForm } from '@mantine/form';

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

export default function TaskForm({ task, onClose, onSubmit, pending, error, allowAutomation = false, allowContext = false }) {
  const [openAutomation, setOpenAutomation] = useState(false);
  const [files, setFiles] = useState([]);
  const [fileError, setFileError] = useState('');
  const [dragging, setDragging] = useState(false);
  useEffect(() => { if (error?.remainingFiles) setFiles(error.remainingFiles); }, [error]);
  function addFiles(selected) {
    if (pending) return;
    if (files.length + selected.length > 5) { setFileError('Можно прикрепить до пяти файлов за раз'); return; }
    if (selected.some((file) => file.size > 10 * 1024 * 1024)) { setFileError('Максимальный размер файла — 10 МБ'); return; }
    setFiles([...files, ...selected]); setFileError('');
  }
  const form = useForm({
    initialValues: {
      title: task?.title ?? '',
      description: task?.description ?? '',
      deadline: task?.deadline ? dayjs.utc(task.deadline).local().format('YYYY-MM-DD HH:mm:ss') : null,
      priority: task?.priority ?? 'medium',
      status: task?.status ?? 'todo',
      category: task?.category ?? '',
      ...(!task && allowContext ? { agent_notes: '' } : {}),
    },
    validate: { title: (value) => value.trim() ? null : 'Укажите название' },
  });

  return (
    <Modal opened onClose={onClose} title={task ? 'Редактировать задачу' : 'Новая задача'} centered size="md">
      <form onSubmit={form.onSubmit((values) => onSubmit({
        ...values,
        title: values.title.trim(),
        deadline: values.deadline ? dayjs(values.deadline).toISOString() : null,
        description: values.description.trim() || null,
        category: values.category.trim() || null,
      }, { openAutomation: allowAutomation && openAutomation, files, taskId: error?.savedTask?.id }))}>
        <Stack gap="xs">
          <TextInput label="Название" placeholder="Что нужно сделать?" required autoFocus {...form.getInputProps('title')} />
          <Textarea label="Описание" placeholder="Коротко о задаче" autosize minRows={1} maxRows={4} {...form.getInputProps('description')} />
          <SimpleGrid cols={{ base: 1, xs: 2 }} spacing="xs">
            <DateTimePicker label="Дедлайн" placeholder="Без срока" valueFormat="DD.MM.YYYY HH:mm" clearable {...form.getInputProps('deadline')} />
            <Select label="Приоритет" data={priorities} allowDeselect={false} {...form.getInputProps('priority')} />
          </SimpleGrid>
          {allowContext && <div className={`task-file-drop ${dragging ? 'file-drop-active' : ''}`} onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); addFiles(Array.from(event.dataTransfer.files)); }}>
            <FileButton multiple onChange={addFiles}>{(props) => <Button {...props} fullWidth variant="subtle" size="xs" disabled={pending}>Перетащите файлы или выберите · до 5 × 10 МБ</Button>}</FileButton>
            {files.map((file, index) => <Group key={`${file.name}:${index}`} justify="space-between" gap="xs"><Text size="xs" style={{ overflowWrap: 'anywhere', flex: 1 }}>{file.name}</Text><Button variant="subtle" size="compact-xs" disabled={pending} onClick={() => setFiles(files.filter((_, number) => number !== index))}>Убрать</Button></Group>)}
          </div>}
          {fileError && <Alert color="red">{fileError}</Alert>}
          <details open={task ? true : undefined}><summary style={{ cursor: 'pointer', fontSize: 12, color: 'var(--mantine-color-dimmed)' }}>Дополнительно</summary><Stack gap="xs" mt="xs">
            {!task && allowContext && <Textarea label="Заметки для Monocle" placeholder="Что проверить и учесть" autosize minRows={1} maxRows={4} maxLength={12000} {...form.getInputProps('agent_notes')} />}
            <SimpleGrid cols={2} spacing="xs"><Select label="Статус" data={statuses} allowDeselect={false} {...form.getInputProps('status')} /><TextInput label="Категория" {...form.getInputProps('category')} /></SimpleGrid>
          </Stack></details>
          {allowAutomation && <Checkbox label="Открыть действия ИИ после сохранения" checked={openAutomation} onChange={(event) => setOpenAutomation(event.currentTarget.checked)} />}
          {error && <Alert color="red">{error.message}</Alert>}
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>Отмена</Button>
            <Button type="submit" loading={pending}>Сохранить</Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
