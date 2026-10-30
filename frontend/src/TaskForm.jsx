import { Alert, Button, Group, Modal, Select, Stack, Textarea, TextInput } from '@mantine/core';
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
  { value: 'done', label: 'Выполнено' },
];

export default function TaskForm({ task, onClose, onSubmit, pending, error }) {
  const form = useForm({
    initialValues: {
      title: task?.title ?? '',
      description: task?.description ?? '',
      deadline: task?.deadline ? dayjs.utc(task.deadline).local().format('YYYY-MM-DD HH:mm:ss') : null,
      priority: task?.priority ?? 'medium',
      status: task?.status ?? 'todo',
      category: task?.category ?? '',
    },
    validate: { title: (value) => value.trim() ? null : 'Укажите название' },
  });

  return (
    <Modal opened onClose={onClose} title={task ? 'Редактировать задачу' : 'Новая задача'} centered>
      <form onSubmit={form.onSubmit((values) => onSubmit({
        ...values,
        title: values.title.trim(),
        deadline: values.deadline ? dayjs(values.deadline).toISOString() : null,
        description: values.description.trim() || null,
        category: values.category.trim() || null,
      }))}>
        <Stack>
          <TextInput label="Название" required {...form.getInputProps('title')} />
          <Textarea label="Описание" autosize minRows={3} {...form.getInputProps('description')} />
          <DateTimePicker label="Дедлайн" placeholder="Выберите дату и время" valueFormat="DD.MM.YYYY HH:mm" clearable {...form.getInputProps('deadline')} />
          <Select label="Приоритет" data={priorities} allowDeselect={false} {...form.getInputProps('priority')} />
          <Select label="Статус" data={statuses} allowDeselect={false} {...form.getInputProps('status')} />
          <TextInput label="Категория" {...form.getInputProps('category')} />
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
