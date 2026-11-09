import { useEffect, useState } from 'react';
import { Alert, Anchor, Button, Group, Loader, Paper, SimpleGrid, Stack, Text, Textarea, TextInput, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';

function buildTree(files) {
  const root = { path: '', name: 'Мои файлы', folder: true, children: Object.create(null) };
  for (const file of files) {
    let parent = root;
    const parts = file.path.split('/');
    parts.forEach((part, index) => {
      const path = parts.slice(0, index + 1).join('/');
      if (!parent.children[part]) parent.children[part] = {
        path, name: part === '@tasks' ? 'Файлы задач' : parts[0] === '@tasks' && index === 1 ? `Задача #${part}` : part,
        folder: true, children: Object.create(null),
      };
      if (index === parts.length - 1) Object.assign(parent.children[part], file);
      parent = parent.children[part];
    });
  }
  return root;
}

function FileTree({ node, selected, onSelect }) {
  if (!node.folder) return <Button size="xs" variant={selected === node.path ? 'light' : 'subtle'} justify="start" h="auto" py={5} onClick={() => onSelect(node)} styles={{ label: { whiteSpace: 'normal', overflowWrap: 'anywhere', textAlign: 'left' } }}>📄 {node.name}</Button>;
  return <details open>
    <summary style={{ cursor: 'pointer', overflowWrap: 'anywhere' }} onClick={() => onSelect(node)}>📁 {node.name}</summary>
    <Stack gap={2} pl="sm" mt={4}>
      {Object.values(node.children).sort((a, b) => Number(b.folder) - Number(a.folder) || a.name.localeCompare(b.name, 'ru')).map((child) => <FileTree key={child.path} node={child} selected={selected} onSelect={onSelect} />)}
    </Stack>
  </details>;
}

export default function Files() {
  const client = useQueryClient();
  const [folder, setFolder] = useState('');
  const [name, setName] = useState('');
  const [editor, setEditor] = useState(() => client.getQueryData(['file-editor']) ?? null);
  const [notice, setNotice] = useState('');
  const dirty = editor?.editable && editor.content !== editor.original;
  useEffect(() => { client.setQueryData(['file-editor'], editor); }, [client, editor]);
  useEffect(() => {
    const warn = (event) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  const tree = useQuery({ queryKey: ['file-tree'], queryFn: () => request('/integrations/context/workspace') });
  const open = useMutation({
    mutationFn: (file) => request(`/integrations/context/workspace/file?path=${encodeURIComponent(file.path)}`),
    onSuccess: (data, file) => setEditor({ ...file, ...data, original: data.content }),
  });
  const refresh = () => client.invalidateQueries({ queryKey: ['file-tree'] });
  const save = useMutation({
    mutationFn: (file) => request('/integrations/context/workspace/file', { method: 'PUT', body: JSON.stringify({ path: file.path, content: file.content, version: file.version }) }),
    onSuccess: (data, file) => {
      setEditor((current) => current?.path === file.path ? { ...current, version: data.version, original: file.content } : current);
      setNotice('Файл сохранён');
      refresh();
      client.invalidateQueries({ queryKey: ['context-file-library'] });
      client.invalidateQueries({ queryKey: ['context-files'] });
      client.invalidateQueries({ queryKey: ['context'] });
      client.invalidateQueries({ queryKey: ['tasks'] });
      client.invalidateQueries({ queryKey: ['jev-ranking'] });
    },
  });
  const createFolder = useMutation({
    mutationFn: (path) => request(`/integrations/context/workspace/folder?path=${encodeURIComponent(path)}`, { method: 'POST' }),
    onSuccess: () => { setName(''); setNotice('Папка создана'); refresh(); },
  });
  const busy = open.isPending || save.isPending || createFolder.isPending;
  function select(file) {
    if (busy) return;
    if (file.folder) { setFolder(file.path.startsWith('@tasks') ? '' : file.path); return; }
    if (dirty && !window.confirm('Открыть другой файл без сохранения изменений?')) return;
    setNotice('');
    save.reset();
    open.reset();
    if (file.editable) open.mutate(file);
    else setEditor(file);
  }
  function newFile() {
    if (dirty && !window.confirm('Создать файл без сохранения текущих изменений?')) return;
    const path = folder ? `${folder}/${name.trim()}` : name.trim();
    setEditor({ path, name: name.trim(), editable: true, content: '', original: null, version: null });
    setName('');
    setNotice('Новый файл: введи текст и нажми «Сохранить»');
    open.reset();
    save.reset();
  }
  const error = tree.error || open.error || save.error || createFolder.error;
  return <Stack>
    <Title order={2}>Мои файлы</Title>
    {error && <Alert color="red">{error.message}</Alert>}
    {notice && <Text size="sm" c="dimmed">{notice}</Text>}
    <SimpleGrid cols={{ base: 1, md: 2 }}>
      <Paper withBorder p="md"><Stack>
        <Text size="sm">Создать в: /{folder}</Text>
        <TextInput label="Имя файла или папки" placeholder="Например: Учёба или заметки.md" value={name} onChange={(event) => setName(event.currentTarget.value)} maxLength={200} />
        <Group><Button variant="light" disabled={busy || !name.trim() || name.includes('/') || name.includes('\\')} onClick={() => createFolder.mutate(folder ? `${folder}/${name.trim()}` : name.trim())}>Создать папку</Button><Button variant="light" disabled={busy || !name.trim() || name.includes('/') || name.includes('\\')} onClick={newFile}>Создать файл</Button></Group>
        {tree.isPending ? <Loader /> : <FileTree node={buildTree(tree.data ?? [])} selected={editor?.path} onSelect={select} />}
      </Stack></Paper>
      <Paper withBorder p="md"><Stack>
        {open.isPending ? <Loader /> : !editor ? <Text c="dimmed">Выбери файл в дереве или создай новый.</Text> : <>
          <Text fw={500} style={{ overflowWrap: 'anywhere' }}>{editor.name}{dirty ? ' · Есть несохранённые изменения' : ''}</Text>
          {editor.editable ? <>
            <Textarea aria-label="Содержимое файла" minRows={18} autosize maxRows={35} value={editor.content} onChange={(event) => { const content = event.currentTarget.value; setEditor((current) => ({ ...current, content })); }} styles={{ input: { fontFamily: 'monospace', tabSize: 2 } }} />
            <Button loading={save.isPending} disabled={!dirty || open.isPending} onClick={() => save.mutate(editor)}>Сохранить файл</Button>
          </> : <Text size="sm">PDF, DOCX и изображения доступны для скачивания. Здесь можно редактировать текстовые файлы до 1 МБ.</Text>}
          {editor.url && <Anchor href={editor.url} download>Скачать файл</Anchor>}
        </>}
      </Stack></Paper>
    </SimpleGrid>
  </Stack>;
}
