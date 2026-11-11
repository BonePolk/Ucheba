import { useRef, useState } from 'react';
import { Alert, Anchor, Button, FileButton, Group, Select, Stack, Text } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';

function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(',')[1]);
    reader.onerror = () => reject(new Error('Не удалось прочитать файл'));
    reader.readAsDataURL(file);
  });
}

export default function ContextFiles({ taskId, disabled }) {
  const client = useQueryClient();
  const reset = useRef(null);
  const [saved, setSaved] = useState(null);
  const path = `/integrations/tasks/${taskId}/context/files`;
  const files = useQuery({ queryKey: ['context-files', taskId], queryFn: () => request(path) });
  const library = useQuery({ queryKey: ['context-file-library'], queryFn: () => request('/integrations/context/files') });
  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ['context-files', taskId] });
    await client.invalidateQueries({ queryKey: ['context-file-library'] });
    await client.invalidateQueries({ queryKey: ['context', taskId] });
    await client.invalidateQueries({ queryKey: ['tasks'] });
  };
  const upload = useMutation({
    mutationFn: async (file) => {
      if (file.size > 10 * 1024 * 1024) throw new Error('Максимальный размер файла — 10 МБ');
      return request(path, { method: 'POST', body: JSON.stringify({ name: file.name, content: await readFile(file) }) });
    },
    onSuccess: refresh,
    onSettled: () => reset.current?.(),
  });
  const remove = useMutation({ mutationFn: (id) => request(`${path}/${id}`, { method: 'DELETE' }), onSuccess: refresh });
  const reuse = useMutation({
    mutationFn: () => {
      const [source_task_id, file_id] = saved.split(':');
      return request(`${path}/reuse`, { method: 'POST', body: JSON.stringify({ source_task_id: Number(source_task_id), file_id }) });
    },
    onSuccess: async () => { setSaved(null); await refresh(); },
  });
  const busy = disabled || upload.isPending || remove.isPending || reuse.isPending;
  const error = files.error || library.error || upload.error || remove.error || reuse.error;
  return <Stack gap="xs">
    <Text fw={500}>Файлы для оценки контекста</Text>
    <Text size="xs" c="dimmed">До 5 вложений по 10 МБ: PDF, DOCX, XLSX, PPTX, текст и изображения. Monocle сама выбирает подходящие документы из вложений и сохранённой библиотеки, открывает их и нужные страницы PDF. Ручной выбор из библиотеки необязателен.</Text>
    {files.data?.map((file) => <Group key={file.id} justify="space-between">
      <Anchor href={`/api${path}/${file.id}`} download>{file.name}{file.pages ? ` · ${file.pages} стр.` : ''}</Anchor>
      <Button size="xs" variant="subtle" color="red" disabled={busy} onClick={() => remove.mutate(file.id)}>Убрать из задачи</Button>
    </Group>)}
    <FileButton resetRef={reset} onChange={(file) => { if (file) upload.mutate(file); }} accept=".pdf,.docx,.xlsx,.pptx,.txt,.md,.csv,.json,.py,.js,.c,.cpp,.h,.java,.sql,.png,.jpg,.jpeg,.webp">
      {(props) => <Button {...props} variant="default" loading={upload.isPending} disabled={busy || files.isPending || files.data?.length >= 5}>Прикрепить файл</Button>}
    </FileButton>
    <Select label="Из сохранённых файлов" placeholder="Найти файл по названию" searchable clearable value={saved} onChange={setSaved} disabled={busy || library.isPending} data={(library.data ?? []).filter((file) => file.task_id !== taskId || !files.data?.some((attached) => attached.id === file.id)).map((file) => ({ value: `${file.task_id}:${file.id}`, label: `${file.name} · задача #${file.task_id}` }))} />
    <Button variant="light" loading={reuse.isPending} disabled={busy || !saved || files.data?.length >= 5} onClick={() => reuse.mutate()}>Использовать сохранённый файл</Button>
    <Text size="xs" c="dimmed">«Убрать из задачи» сохраняет оригинал в библиотеке.</Text>
    {error && <Alert color="red">{error.message}</Alert>}
  </Stack>;
}
