import { useEffect, useState } from 'react';
import { Alert, Badge, Breadcrumbs, Button, FileButton, Group, Loader, Modal, Paper, SegmentedControl, Select, Stack, Text, Textarea, TextInput, Title } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';
import { fileContent } from './fileUpload';

function buildTree(files) {
  const root = { path: '', name: 'Все файлы', folder: true, children: Object.create(null) };
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

function fileType(file) {
  if (file.folder) return 'folder';
  const extension = file.name.split('.').at(-1).toLowerCase();
  if (['xlsx', 'xls', 'ods', 'csv', 'tsv'].includes(extension)) return 'table';
  if (['png', 'jpg', 'jpeg', 'webp', 'gif'].includes(extension)) return 'image';
  if (extension === 'pdf') return 'pdf';
  return 'document';
}

function FileIcon({ file }) {
  const type = fileType(file);
  return <span className={`file-icon file-icon-${type}`} aria-hidden="true"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round">
    {type === 'folder' ? <path d="M3 7V5h6l2 2h10v12H3V7Z" /> : <><path d="M5 3h9l5 5v13H5V3Z M14 3v6h5" />{type === 'table' ? <path d="M8 12h8M8 16h8M11 10v9" /> : type === 'image' ? <path d="m7 18 4-5 2 2 2-3 3 6M9 10h.01" /> : <path d="M8 13h8M8 17h5" />}</>}
  </svg></span>;
}

function sizeLabel(size) {
  if (size == null) return '—';
  if (size < 1024) return `${size} Б`;
  return size < 1024 * 1024 ? `${(size / 1024).toFixed(1)} КБ` : `${(size / 1024 / 1024).toFixed(1)} МБ`;
}

function FolderTree({ node, current, onSelect }) {
  const folders = Object.values(node.children).filter((child) => child.folder).sort((a, b) => a.name.localeCompare(b.name, 'ru'));
  if (!folders.length) return null;
  return <div className="file-folders">{folders.map((child) => <div key={child.path}>
    <button className={`file-nav ${current === child.path ? 'is-selected' : ''}`} onClick={() => onSelect(child.path)}><FileIcon file={child} /><span>{child.name}</span></button>
    {current.startsWith(child.path + '/') || current === child.path ? <FolderTree node={child} current={current} onSelect={onSelect} /> : null}
  </div>)}</div>;
}

export default function Files() {
  const compact = useMediaQuery('(max-width: 767px)', window.innerWidth < 768);
  const [foldersOpen, setFoldersOpen] = useState(false);
  const client = useQueryClient();
  const [folder, setFolder] = useState('');
  const [search, setSearch] = useState('');
  const [view, setView] = useState('grid');
  const [sort, setSort] = useState('name');
  const [name, setName] = useState('');
  const [creating, setCreating] = useState(null);
  const [editor, setEditor] = useState(() => client.getQueryData(['file-editor']) ?? null);
  const [mode, setMode] = useState('preview');
  const [page, setPage] = useState(1);
  const [sheet, setSheet] = useState('');
  const [offset, setOffset] = useState(0);
  const [notice, setNotice] = useState('');
  const [dragging, setDragging] = useState(false);
  const dirty = editor?.editable && editor.content !== editor.original;
  useEffect(() => { client.setQueryData(['file-editor'], editor); }, [client, editor]);
  useEffect(() => {
    const warn = (event) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  const tree = useQuery({ queryKey: ['file-tree'], queryFn: () => request('/integrations/context/workspace'), refetchInterval: 10000 });
  const files = Array.isArray(tree.data) ? tree.data : tree.data?.files ?? [];
  const root = buildTree(files);
  let current = root;
  for (const part of folder.split('/').filter(Boolean)) current = current.children[part] ?? root;
  const rows = (search.trim() ? files.filter((file) => file.name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()) && (!folder || file.path.startsWith(folder + '/'))) : Object.values(current.children))
    .sort((a, b) => Number(b.folder) - Number(a.folder) || (sort === 'size' ? (b.size ?? 0) - (a.size ?? 0) : sort === 'recent' ? (b.modified_at ?? '').localeCompare(a.modified_at ?? '') : 0) || a.name.localeCompare(b.name, 'ru'));
  const total = files.filter((file) => !file.folder);
  const refresh = () => client.invalidateQueries({ queryKey: ['file-tree'] });
  const open = useMutation({
    mutationFn: (file) => request(`/integrations/context/workspace/file?path=${encodeURIComponent(file.path)}`),
    onSuccess: (data, file) => setEditor({ ...file, ...data, original: data.content }),
  });
  const preview = useQuery({
    queryKey: ['file-preview', editor?.path, editor?.version, page, sheet, offset],
    queryFn: () => request(`/integrations/context/workspace/preview?${new URLSearchParams({ path: editor.path, page, sheet, offset })}`),
    enabled: Boolean(editor?.url) && mode === 'preview',
    retry: false,
  });
  const save = useMutation({
    mutationFn: (file) => request('/integrations/context/workspace/file', { method: 'PUT', body: JSON.stringify({ path: file.path, content: file.content, version: file.version }) }),
    onSuccess: (data, file) => {
      setEditor((current) => current?.path === file.path ? { ...current, version: data.version, original: file.content, url: current.url ?? `/api/integrations/context/workspace/download?path=${encodeURIComponent(file.path)}` } : current);
      setNotice('Файл сохранён в Silo');
      refresh();
      for (const key of ['context-file-library', 'context-files', 'context', 'tasks', 'ameba-ranking']) client.invalidateQueries({ queryKey: [key] });
    },
  });
  const deleteFile = useMutation({
    mutationFn: (file) => request(`/integrations/context/workspace/file?path=${encodeURIComponent(file.path)}`, { method: 'DELETE' }),
    onSuccess: (_, file) => {
      setEditor((current) => current?.path === file.path ? null : current);
      setNotice(`Файл «${file.name}» удалён`);
      refresh();
    },
  });
  const createFolder = useMutation({
    mutationFn: (path) => request(`/integrations/context/workspace/folder?path=${encodeURIComponent(path)}`, { method: 'POST' }),
    onSuccess: () => { setCreating(null); setName(''); refresh(); },
  });
  const upload = useMutation({
    mutationFn: async ({ files, target }) => {
      if (target.startsWith('@')) throw new Error('Выберите личную папку для загрузки');
      for (const file of files) {
        await request('/integrations/context/workspace/upload', { method: 'POST', body: JSON.stringify({
          path: target ? `${target}/${file.name}` : file.name, content: await fileContent(file),
        }) });
      }
    },
    onSuccess: () => setNotice('Файлы сохранены в Silo'),
    onSettled: refresh,
  });
  function dropFiles(event, target = folder) {
    event.preventDefault(); event.stopPropagation(); setDragging(false);
    if (!busy && event.dataTransfer.files.length) upload.mutate({ files: Array.from(event.dataTransfer.files), target });
  }
  const busy = open.isPending || save.isPending || deleteFile.isPending || createFolder.isPending || upload.isPending;
  function select(file) {
    if (busy) return;
    if (file.folder) { setFolder(file.path); setSearch(''); return; }
    if (dirty && !window.confirm('Открыть другой файл без сохранения изменений?')) return;
    setNotice(''); open.reset(); save.reset(); setMode('preview'); setPage(1); setSheet(''); setOffset(0);
    if (file.editable) open.mutate(file);
    else setEditor(file);
  }
  function create() {
    const path = folder ? `${folder}/${name.trim()}` : name.trim();
    if (creating === 'folder') { createFolder.mutate(path); return; }
    if (dirty && !window.confirm('Создать файл без сохранения текущих изменений?')) return;
    setEditor({ path, name: name.trim(), editable: true, content: '', original: null, version: null });
    setMode('edit'); setCreating(null); setName(''); open.reset(); save.reset();
  }
  const data = preview.data;
  const columns = [...new Set((data?.table?.rows ?? []).flatMap((row) => Object.keys(row.cells)))];
  const error = tree.error || open.error || save.error || deleteFile.error || createFolder.error || upload.error;
  return <Stack gap="md">
    <Group justify="space-between"><div><Title order={2}>Мои файлы</Title><Text c="dimmed" size="sm">{total.length} файлов · {sizeLabel(total.reduce((sum, file) => sum + (file.size ?? 0), 0))}</Text></div><Button variant="subtle" loading={tree.isFetching} onClick={refresh}>Обновить</Button></Group>
    {error && <Alert color="red">{error.message}</Alert>}
    {tree.data?.warning && <Alert color="yellow">{tree.data.warning}</Alert>}
    {notice && <Text size="sm" c="dimmed">{notice}</Text>}
    <Paper withBorder radius="md" className={`file-explorer ${editor ? 'has-preview' : ''} ${dragging ? 'file-drop-active' : ''}`} onDragOver={(event) => { if (event.dataTransfer.types.includes('Files')) { event.preventDefault(); setDragging(true); } }} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setDragging(false); }} onDrop={dropFiles}>
      <aside className="file-sidebar">{compact && <Button fullWidth variant="subtle" onClick={() => setFoldersOpen(!foldersOpen)} aria-expanded={foldersOpen}>{foldersOpen ? 'Скрыть папки' : 'Папки'} {foldersOpen ? '−' : '+'}</Button>}{(!compact || foldersOpen) && <><Text size="xs" fw={700} c="dimmed" mb="sm" tt="uppercase">Библиотека</Text>
        <button className={`file-nav ${!folder ? 'is-selected' : ''}`} onClick={() => { setFolder(''); setSearch(''); }}><FileIcon file={{ folder: true }} /><span>Все файлы</span><small>{total.length}</small></button>
        <FolderTree node={root} current={folder} onSelect={(path) => { setFolder(path); setSearch(''); }} />
      </>} </aside>
      <section className="file-main">
        <div className="file-toolbar"><Group justify="space-between" gap="xs"><Breadcrumbs separator="/" style={{ minWidth: 0 }}><Button size="compact-xs" variant="subtle" onClick={() => setFolder('')}>Файлы</Button>{folder.split('/').filter(Boolean).map((part, index, parts) => <Button key={index} size="compact-xs" variant="subtle" onClick={() => setFolder(parts.slice(0, index + 1).join('/'))}>{part === '@tasks' ? 'Файлы задач' : part}</Button>)}</Breadcrumbs>
          <Group gap={6}><FileButton multiple onChange={(files) => { if (files.length) upload.mutate({ files, target: folder }); }}>{(props) => <Button {...props} size="compact-xs" variant="default" loading={upload.isPending} disabled={busy || folder.startsWith('@')}>Загрузить</Button>}</FileButton><Button size="compact-xs" variant="light" disabled={busy || folder.startsWith('@')} onClick={() => { setName(''); setCreating('folder'); }}>Создать папку</Button><Button size="compact-xs" disabled={busy || folder.startsWith('@')} onClick={() => { setName(''); setCreating('file'); }}>Создать файл</Button></Group>
        </Group><Group gap="xs" mt="sm"><TextInput aria-label="Поиск файлов" placeholder="Поиск по имени…" value={search} onChange={(event) => setSearch(event.currentTarget.value)} style={{ flex: 1, minWidth: 140 }} /><Select aria-label="Сортировка файлов" value={sort} onChange={setSort} data={[{ value: 'name', label: 'По имени' }, { value: 'recent', label: 'Сначала новые' }, { value: 'size', label: 'По размеру' }]} w={145} /><SegmentedControl size="xs" aria-label="Вид файлов" value={view} onChange={setView} data={[{ value: 'grid', label: 'Плитки' }, { value: 'list', label: 'Список' }]} /></Group></div>
        {tree.isPending ? <div className="file-empty"><Loader /></div> : rows.length ? <div className={`file-items file-items-${view}`}>{rows.map((file) => <button key={file.path} disabled={busy} className={`file-item ${editor?.path === file.path ? 'is-selected' : ''}`} onClick={() => select(file)} onDragOver={(event) => { if (file.folder) event.preventDefault(); }} onDrop={(event) => { if (file.folder) dropFiles(event, file.path); }} title={file.path}>
          <FileIcon file={file} /><span className="file-item-name">{file.name}</span><span className="file-item-meta">{file.folder ? 'Папка' : sizeLabel(file.size)}{view === 'grid' && !file.folder && <span> · {file.name.includes('.') ? file.name.split('.').at(-1).toUpperCase() : 'Файл'}</span>}</span>
        </button>)}</div> : <div className="file-empty"><FileIcon file={{ folder: true }} /><Text fw={600}>{search ? 'Ничего не найдено' : 'Пока пусто'}</Text><Text size="sm" c="dimmed">{search ? 'Попробуйте другое имя файла.' : 'Создайте файл или попросите агента скачать документ.'}</Text></div>}
      </section>
      {editor && <aside className="file-preview"><Group justify="space-between" wrap="nowrap"><Group gap="xs" wrap="nowrap"><FileIcon file={editor} /><Text fw={600} size="sm" style={{ overflowWrap: 'anywhere' }}>{editor.name}</Text></Group><Button variant="subtle" size="compact-xs" disabled={busy} aria-label="Закрыть просмотр" onClick={() => { if (!dirty || window.confirm('Закрыть без сохранения изменений?')) setEditor(null); }}>✕</Button></Group>
        <Text size="xs" c="dimmed" mt="xs" style={{ overflowWrap: 'anywhere' }}>{editor.path}</Text>
        <Group my="sm" gap="xs">{editor.url && <Button component="a" href={editor.url} download variant="light" size="compact-xs">Скачать</Button>}{editor.url && !editor.path.startsWith('@tasks/') && <Button color="red" variant="light" size="compact-xs" loading={deleteFile.isPending} disabled={busy} onClick={() => { const changes = dirty ? ' Несохранённые изменения будут потеряны.' : ''; if (window.confirm(`Удалить файл «${editor.name}» без возможности восстановления?${changes}`)) deleteFile.mutate(editor); }}>Удалить</Button>}{editor.editable && <SegmentedControl size="xs" value={mode} onChange={setMode} data={[{ value: 'preview', label: 'Просмотр' }, { value: 'edit', label: 'Редактор' }]} />}{dirty && <Badge size="xs" variant="dot">Не сохранено</Badge>}</Group>
        {open.isPending ? <Loader /> : mode === 'edit' && editor.editable ? <Stack gap="sm"><Textarea aria-label="Содержимое файла" minRows={18} autosize maxRows={30} value={editor.content} onChange={(event) => { const content = event.currentTarget.value; setEditor((current) => ({ ...current, content })); }} styles={{ input: { fontFamily: 'monospace', fontSize: 12 } }} /><Button loading={save.isPending} disabled={!dirty || open.isPending} onClick={() => save.mutate(editor)}>Сохранить файл</Button></Stack> : <Stack gap="sm">
          {preview.isFetching && <Loader size="sm" />}{preview.error && <Alert color="yellow">{preview.error.message}</Alert>}
          {data?.notice && <Text size="xs" c="dimmed">{data.notice}</Text>}
          {data?.image && <img className="file-preview-image" src={`data:${data.mime ?? 'image/jpeg'};base64,${data.image}`} alt={editor.name} />}
          {data?.pages > 1 && <Group justify="space-between"><Button size="compact-xs" variant="default" disabled={page <= 1} onClick={() => setPage(page - 1)}>←</Button><Text size="xs">Страница {page} из {data.pages}</Text><Button size="compact-xs" variant="default" disabled={page >= data.pages} onClick={() => setPage(page + 1)}>→</Button></Group>}
          {data?.table && <><Select size="xs" aria-label="Лист таблицы" value={sheet || data.table.sheets[0] || null} data={data.table.sheets} onChange={(value) => { setSheet(value ?? ''); setOffset(0); }} /><div className="file-table-scroll"><table className="file-table"><thead><tr><th>№</th>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{data.table.rows.map((row) => <tr key={`${row.sheet}:${row.row}`}><th>{row.row}</th>{columns.map((column) => <td key={column}>{row.cells[column]}</td>)}</tr>)}</tbody></table></div><Group justify="space-between"><Button size="compact-xs" variant="default" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 40))}>←</Button><Text size="xs">{offset + 1}–{offset + data.table.rows.length} из {data.table.matched}</Text><Button size="compact-xs" variant="default" disabled={data.table.next_offset == null} onClick={() => setOffset(data.table.next_offset)}>→</Button></Group></>}
          {data?.text && !data.image && <pre className="file-text-preview">{data.text}</pre>}
          {!preview.isFetching && !preview.error && !data?.text && !data?.image && !data?.table && <Text size="sm" c="dimmed">Предпросмотр недоступен. Файл можно скачать.</Text>}
        </Stack>}
      </aside>}
    </Paper>
    <Modal opened={Boolean(creating)} onClose={() => setCreating(null)} title={creating === 'folder' ? 'Новая папка' : 'Новый файл'} size="sm"><Stack><Text size="xs" c="dimmed">/{folder || 'Мои файлы'}</Text><TextInput label="Название" placeholder={creating === 'folder' ? 'Например: Учёба' : 'Например: заметки.md'} value={name} onChange={(event) => setName(event.currentTarget.value)} maxLength={200} autoFocus /><Button loading={createFolder.isPending} disabled={!name.trim() || name.includes('/') || name.includes('\\')} onClick={create}>Создать</Button></Stack></Modal>
  </Stack>;
}
