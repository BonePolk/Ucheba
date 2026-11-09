import { useRef } from 'react';
import { Alert, Anchor, Button, FileButton, Group, Stack, Text } from '@mantine/core';
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
  const path = `/integrations/tasks/${taskId}/context/files`;
  const files = useQuery({ queryKey: ['context-files', taskId], queryFn: () => request(path) });
  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ['context-files', taskId] });
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
  const error = files.error || upload.error || remove.error;
  return <Stack gap="xs">
    <Text fw={500}>Файлы для оценки контекста</Text>
    <Text size="xs" c="dimmed">До 5 файлов по 10 МБ: PDF, DOCX, текст, PNG, JPG, WEBP. Текст документов и изображения будут переданы Luna. PDF-сканы прикрепляйте как изображения.</Text>
    {files.data?.map((file) => <Group key={file.id} justify="space-between">
      <Anchor href={`/api${path}/${file.id}`} download>{file.name}</Anchor>
      <Button size="xs" variant="subtle" color="red" disabled={disabled || upload.isPending || remove.isPending} onClick={() => remove.mutate(file.id)}>Убрать</Button>
    </Group>)}
    <FileButton resetRef={reset} onChange={(file) => { if (file) upload.mutate(file); }} accept=".pdf,.docx,.txt,.md,.csv,.json,.py,.js,.c,.cpp,.h,.java,.sql,.png,.jpg,.jpeg,.webp">
      {(props) => <Button {...props} variant="default" loading={upload.isPending} disabled={disabled || remove.isPending || files.isPending || files.data?.length >= 5}>Прикрепить файл</Button>}
    </FileButton>
    {error && <Alert color="red">{error.message}</Alert>}
  </Stack>;
}
