import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Archive,
  ChevronLeft,
  ChevronRight,
  Download,
  File,
  FilePlus,
  FileSpreadsheet,
  FileText,
  Folder,
  FolderPlus,
  Image as ImageIcon,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  X,
} from 'lucide-react';
import { request } from './api';
import { fileContent, safeFileName, uploadTaskFiles } from './fileUpload';
import { useMediaQuery } from '@/lib/utils';
import { CollapseSection, SlidePanel } from '@/lib/motion';
import { Button, IconButton } from '@/components/ui/button';
import { Checkbox, FileTrigger } from '@/components/ui/controls';
import { Modal } from '@/components/ui/dialog';
import { Field, Input, Textarea } from '@/components/ui/input';
import { SelectField } from '@/components/ui/select';
import { Badge, EmptyState, Notice, PageHeader, Spinner } from '@/components/ui/surface';
import { SegmentedControl } from '@/components/ui/tabs';

function buildTree(files) {
  const root = { path: '', name: 'Все файлы', folder: true, children: Object.create(null) };
  for (const file of files) {
    let parent = root;
    const parts = file.path.split('/');
    parts.forEach((part, index) => {
      const path = parts.slice(0, index + 1).join('/');
      if (!parent.children[part]) {
        parent.children[part] = {
          path,
          name: part === '@tasks' ? 'Файлы задач' : parts[0] === '@tasks' && index === 1 ? `Задача #${part}` : part,
          folder: true,
          children: Object.create(null),
        };
      }
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

function FileIcon({ file, size = 16 }) {
  const type = fileType(file);
  const props = { style: { width: size, height: size }, className: 'shrink-0', 'aria-hidden': true };
  if (type === 'folder') return <Folder {...props} className="shrink-0 text-[#CFA9F5]" />;
  if (type === 'table') return <FileSpreadsheet {...props} className="shrink-0 text-[#6EBE86]" />;
  if (type === 'image') return <ImageIcon {...props} className="shrink-0 text-[#6FA3CC]" />;
  if (type === 'pdf') return <FileText {...props} className="shrink-0 text-[#D46A6A]" />;
  return <File {...props} className="shrink-0 text-[#929292]" />;
}

function sizeLabel(size) {
  if (size == null) return '—';
  if (size < 1024) return `${size} Б`;
  return size < 1024 * 1024 ? `${(size / 1024).toFixed(1)} КБ` : `${(size / 1024 / 1024).toFixed(1)} МБ`;
}

function FolderTree({ node, current, onSelect }) {
  const folders = Object.values(node.children)
    .filter((child) => child.folder)
    .sort((a, b) => a.name.localeCompare(b.name, 'ru'));
  if (!folders.length) return null;

  return (
    <div className="flex flex-col gap-0.5 pl-2 border-l border-[#222222] ml-1.5">
      {folders.map((child) => {
        const selected = current === child.path;
        const expanded = current.startsWith(child.path + '/') || selected;
        return (
          <div key={child.path}>
            <button
              type="button"
              onClick={() => onSelect(child.path)}
              className={`flex items-center gap-2 w-full h-7 px-2 text-left text-[12px] cursor-pointer transition-colors ${selected ? 'bg-[#7F49B4]/18 text-[#EAEAEA]' : 'text-[#929292] hover:text-[#EAEAEA] hover:bg-[#191919]'}`}
            >
              <FileIcon file={child} size={14} />
              <span className="truncate">{child.name}</span>
            </button>
            <CollapseSection open={expanded}>
              <FolderTree node={child} current={current} onSelect={onSelect} />
            </CollapseSection>
          </div>
        );
      })}
    </div>
  );
}

export default function Files() {
  const compact = useMediaQuery('(max-width: 767px)', typeof window !== 'undefined' ? window.innerWidth < 768 : false);
  const [foldersOpen, setFoldersOpen] = useState(false);
  const client = useQueryClient();
  const [folder, setFolder] = useState('');
  const [search, setSearch] = useState('');
  const [view, setView] = useState('list');
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
  const [selectedPaths, setSelectedPaths] = useState([]);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archivePath, setArchivePath] = useState('');

  const dirty = editor?.editable && editor.content !== editor.original;
  useEffect(() => { client.setQueryData(['file-editor'], editor); }, [client, editor]);
  useEffect(() => {
    const warn = (event) => {
      if (dirty) {
        event.preventDefault();
        event.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const tree = useQuery({
    queryKey: ['file-tree'],
    queryFn: () => request('/integrations/monocle/workspace'),
    refetchInterval: 10000,
  });
  const files = Array.isArray(tree.data) ? tree.data : tree.data?.files ?? [];
  const root = buildTree(files);
  let current = root;
  for (const part of folder.split('/').filter(Boolean)) current = current.children[part] ?? root;

  const rows = (
    search.trim()
      ? files.filter((file) => file.name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()) && (!folder || file.path.startsWith(folder + '/')))
      : Object.values(current.children)
  ).sort(
    (a, b) => Number(b.folder) - Number(a.folder)
      || (sort === 'size' ? (b.size ?? 0) - (a.size ?? 0) : sort === 'recent' ? (b.modified_at ?? '').localeCompare(a.modified_at ?? '') : 0)
      || a.name.localeCompare(b.name, 'ru'),
  );
  const total = files.filter((file) => !file.folder);
  const refresh = () => client.invalidateQueries({ queryKey: ['file-tree'] });

  const open = useMutation({
    mutationFn: (file) => request(`/integrations/monocle/workspace/file?path=${encodeURIComponent(file.path)}`),
    onSuccess: (data, file) => setEditor({ ...file, ...data, original: data.content }),
  });

  const preview = useQuery({
    queryKey: ['file-preview', editor?.path, editor?.version, page, sheet, offset],
    queryFn: () => request(`/integrations/monocle/workspace/preview?${new URLSearchParams({ path: editor.path, page, sheet, offset })}`),
    enabled: Boolean(editor?.url) && mode === 'preview',
    placeholderData: (previous, query) => (query?.queryKey[1] === editor?.path ? previous : undefined),
    retry: false,
  });

  const save = useMutation({
    mutationFn: (file) => request('/integrations/monocle/workspace/file', {
      method: 'PUT',
      body: JSON.stringify({ path: file.path, content: file.content, version: file.version }),
    }),
    onSuccess: (data, file) => {
      setEditor((curr) => (curr?.path === file.path
        ? { ...curr, version: data.version, original: file.content, url: curr.url ?? `/api/integrations/monocle/workspace/download?path=${encodeURIComponent(file.path)}` }
        : curr));
      setNotice('Файл сохранён в Silo');
      refresh();
      for (const key of ['context-file-library', 'context-files', 'context', 'tasks', 'ameba-ranking']) {
        client.invalidateQueries({ queryKey: [key] });
      }
    },
  });

  const deleteFile = useMutation({
    mutationFn: (file) => request(`/integrations/monocle/workspace/file?path=${encodeURIComponent(file.path)}`, { method: 'DELETE' }),
    onSuccess: (_, file) => {
      setEditor((curr) => (curr?.path === file.path ? null : curr));
      setSelectedPaths((paths) => paths.filter((path) => path !== file.path));
      setNotice(`Файл «${file.name}» удалён`);
      refresh();
    },
  });

  const createFolder = useMutation({
    mutationFn: (path) => request(`/integrations/monocle/workspace/folder?path=${encodeURIComponent(path)}`, { method: 'POST' }),
    onSuccess: () => {
      setCreating(null);
      setName('');
      refresh();
    },
  });

  const archive = useMutation({
    mutationFn: (body) => request('/integrations/monocle/workspace/archive', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: (result) => {
      setNotice(`Архив сохранён: ${result.path}`);
      setSelectedPaths([]);
      setArchiveOpen(false);
      refresh();
    },
  });

  const upload = useMutation({
    mutationFn: async ({ files: list, target }) => {
      if (target === '@tasks') {
        throw new Error('Откройте конкретную задачу в «Файлах задач» или выберите личную папку');
      }
      if (target.startsWith('@tasks/')) {
        const taskId = Number(target.split('/')[1]);
        if (!Number.isInteger(taskId) || taskId <= 0) {
          throw new Error('Выберите папку конкретной задачи для загрузки');
        }
        await uploadTaskFiles(taskId, list);
        return { targetTask: taskId };
      }
      for (const file of list) {
        const fileName = safeFileName(file.name, 200);
        const path = target ? `${target}/${fileName}` : fileName;
        const content = await fileContent(file);
        try {
          await request('/integrations/monocle/workspace/upload', {
            method: 'POST',
            body: JSON.stringify({ path, content }),
          });
        } catch (err) {
          if (err?.status === 409 && window.confirm(`Файл «${fileName}» уже существует. Заменить его?`)) {
            await request('/integrations/monocle/workspace/upload', {
              method: 'POST',
              body: JSON.stringify({ path, content, overwrite: true }),
            });
          } else {
            throw err;
          }
        }
      }
      return { targetTask: null };
    },
    onSuccess: (res) => {
      setNotice(res?.targetTask ? `Файлы прикреплены к задаче #${res.targetTask}` : 'Файлы сохранены в Silo');
      if (res?.targetTask) {
        for (const key of ['context-file-library', 'context-files', 'context', 'tasks']) {
          client.invalidateQueries({ queryKey: [key] });
        }
      }
    },
    onSettled: refresh,
  });

  function dropFiles(event, target = folder) {
    event.preventDefault();
    event.stopPropagation();
    setDragging(false);
    if (!busy && event.dataTransfer.files.length) upload.mutate({ files: Array.from(event.dataTransfer.files), target });
  }

  const busy = open.isPending || save.isPending || deleteFile.isPending || createFolder.isPending || upload.isPending || archive.isPending;

  function select(file) {
    if (busy) return;
    if (file.folder) {
      setFolder(file.path);
      setSearch('');
      return;
    }
    if (dirty && !window.confirm('Открыть другой файл без сохранения изменений?')) return;
    setNotice('');
    open.reset();
    save.reset();
    setMode('preview');
    setPage(1);
    setSheet('');
    setOffset(0);
    if (file.editable) open.mutate(file);
    else setEditor(file);
  }

  function create() {
    const path = folder ? `${folder}/${name.trim()}` : name.trim();
    if (creating === 'folder') {
      createFolder.mutate(path);
      return;
    }
    if (dirty && !window.confirm('Создать файл без сохранения текущих изменений?')) return;
    setEditor({ path, name: name.trim(), editable: true, content: '', original: null, version: null });
    setMode('edit');
    setCreating(null);
    setName('');
    open.reset();
    save.reset();
  }

  const data = preview.data;
  const columns = [...new Set((data?.table?.rows ?? []).flatMap((row) => Object.keys(row.cells)))];
  const error = tree.error || open.error || save.error || deleteFile.error || createFolder.error || upload.error || archive.error;

  const gridClass = editor
    ? 'grid-cols-1 lg:grid-cols-[190px_minmax(0,1fr)] xl:grid-cols-[190px_minmax(0,1fr)_minmax(340px,42%)]'
    : 'grid-cols-1 lg:grid-cols-[190px_minmax(0,1fr)]';

  return (
    <div className="flex flex-col gap-3">
      <PageHeader
        title="Файлы"
        count={total.length}
        subtitle={`${sizeLabel(total.reduce((sum, file) => sum + (file.size ?? 0), 0))}`}
        actions={(
          <Button variant="default" leftIcon={<RefreshCw className={`h-3.5 w-3.5 ${tree.isFetching ? 'animate-spin' : ''}`} />} onClick={refresh}>
            Обновить
          </Button>
        )}
      />

      {error && <Notice tone="error">{error.message}</Notice>}
      {tree.data?.warning && <Notice tone="warning">{tree.data.warning}</Notice>}
      {notice && <Notice tone="info" onClose={() => setNotice('')}>{notice}</Notice>}

      <div
        className={`grid ${gridClass} border border-[#303030] bg-[#111111] min-h-[520px] ${dragging ? 'file-drop-active' : ''}`}
        onDragOver={(event) => {
          if (event.dataTransfer.types.includes('Files')) {
            event.preventDefault();
            setDragging(true);
          }
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) setDragging(false);
        }}
        onDrop={dropFiles}
      >
        {/* Left Folder Tree */}
        {(!compact || !editor) && (
          <aside className="border-b lg:border-b-0 lg:border-r border-[#242424] bg-[#0D0D0D] p-2.5 flex flex-col gap-1.5">
            {compact && (
              <Button size="sm" variant="default" className="w-full" onClick={() => setFoldersOpen(!foldersOpen)}>
                {foldersOpen ? 'Скрыть папки' : 'Папки'}
              </Button>
            )}
            {(!compact || foldersOpen) && (
              <>
                <div className="text-[11px] font-medium uppercase tracking-wider text-[#929292] px-1.5 pb-1">Библиотека</div>
                <button
                  type="button"
                  onClick={() => { setFolder(''); setSearch(''); }}
                  className={`flex items-center justify-between gap-2 w-full h-7 px-2 text-left text-[12px] cursor-pointer ${!folder ? 'bg-[#7F49B4]/18 text-[#EAEAEA]' : 'text-[#929292] hover:text-[#EAEAEA] hover:bg-[#191919]'}`}
                >
                  <span className="flex items-center gap-2 truncate">
                    <Folder className="h-3.5 w-3.5 text-[#CFA9F5] shrink-0" />
                    <span>Все файлы</span>
                  </span>
                  <span className="text-[11px] text-[#787878] tabular-nums">{total.length}</span>
                </button>
                <FolderTree
                  node={root}
                  current={folder}
                  onSelect={(path) => {
                    setFolder(path);
                    setSearch('');
                  }}
                />
              </>
            )}
          </aside>
        )}

        {/* Center File Contents */}
        {(!compact || !editor) && (
          <section className="min-w-0 flex flex-col">
            {/* Toolbar */}
            <div className="border-b border-[#242424] p-2.5 flex flex-col gap-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-1 text-[12px] min-w-0">
                  <Button size="xs" variant="subtle" onClick={() => setFolder('')}>Файлы</Button>
                  {folder.split('/').filter(Boolean).map((part, index, parts) => (
                    <span key={index} className="inline-flex items-center gap-1">
                      <span className="text-[#666666]">/</span>
                      <Button size="xs" variant="subtle" onClick={() => setFolder(parts.slice(0, index + 1).join('/'))}>
                        {part === '@tasks' ? 'Файлы задач' : part}
                      </Button>
                    </span>
                  ))}
                </div>

                <div className="flex flex-wrap items-center gap-1.5">
                  <FileTrigger multiple onChange={(list) => { if (list.length) upload.mutate({ files: list, target: folder }); }}>
                    {(props) => (
                      <Button
                        {...props}
                        size="sm"
                        variant="default"
                        leftIcon={<Upload className="h-3 w-3" />}
                        loading={upload.isPending}
                        disabled={busy || folder === '@tasks'}
                        title={folder === '@tasks' ? 'Откройте конкретную задачу или выберите личную папку' : undefined}
                      >
                        Загрузить
                      </Button>
                    )}
                  </FileTrigger>
                  <Button size="sm" variant="default" leftIcon={<FolderPlus className="h-3 w-3" />} disabled={busy || folder.startsWith('@')} onClick={() => { setName(''); setCreating('folder'); }}>
                    Папка
                  </Button>
                  <Button size="sm" variant="primary" leftIcon={<FilePlus className="h-3 w-3" />} disabled={busy || folder.startsWith('@')} onClick={() => { setName(''); setCreating('file'); }}>
                    Файл
                  </Button>
                  {selectedPaths.length > 0 && (
                    <Button
                      size="sm"
                      variant="accent-subtle"
                      leftIcon={<Archive className="h-3 w-3" />}
                      disabled={busy || selectedPaths.length < 2}
                      onClick={() => {
                        setArchivePath(`${folder && !folder.startsWith('@') ? folder + '/' : ''}archive.zip`);
                        setArchiveOpen(true);
                      }}
                    >
                      В ZIP ({selectedPaths.length})
                    </Button>
                  )}
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <Input
                  aria-label="Поиск файлов"
                  placeholder="Поиск по имени…"
                  value={search}
                  onChange={(event) => setSearch(event.currentTarget.value)}
                  leftIcon={<Search className="h-3.5 w-3.5" />}
                  className="flex-1 min-w-[140px]"
                />
                <SelectField
                  ariaLabel="Сортировка файлов"
                  value={sort}
                  onChange={(val) => { if (val) setSort(val); }}
                  options={[
                    { value: 'name', label: 'По имени' },
                    { value: 'recent', label: 'Сначала новые' },
                    { value: 'size', label: 'По размеру' },
                  ]}
                  className="w-[140px]"
                />
                <SegmentedControl
                  ariaLabel="Вид файлов"
                  value={view}
                  onChange={setView}
                  options={[{ value: 'list', label: 'Список' }, { value: 'grid', label: 'Плитки' }]}
                />
              </div>
            </div>

            {/* File list or grid */}
            <div className="p-2.5 flex-1">
              {tree.isPending ? (
                <div className="py-12 flex justify-center"><Spinner label="Загрузка файлов…" /></div>
              ) : rows.length === 0 ? (
                <EmptyState
                  icon={<Folder className="h-5 w-5" />}
                  title={search ? 'Ничего не найдено' : 'Папка пуста'}
                />
              ) : view === 'list' ? (
                <div className="border border-[#242424] divide-y divide-[#1D1D1D] bg-[#0D0D0D]">
                  {rows.map((file) => {
                    const checked = selectedPaths.includes(file.path);
                    const selected = editor?.path === file.path || checked;
                    return (
                      <div
                        key={file.path}
                        className={`flex items-center gap-2 px-2.5 py-1.5 transition-colors ${selected ? 'bg-[#7F49B4]/16' : 'hover:bg-[#161616]'}`}
                        onDragOver={(event) => { if (file.folder) event.preventDefault(); }}
                        onDrop={(event) => { if (file.folder) dropFiles(event, file.path); }}
                      >
                        <div className="w-5 shrink-0 flex items-center justify-center">
                          {!file.folder && !file.path.startsWith('@tasks/') && (
                            <Checkbox
                              aria-label={`Добавить ${file.name} в архив`}
                              checked={checked}
                              disabled={busy}
                              onCheckedChange={(next) => {
                                setSelectedPaths((paths) => (next ? [...paths, file.path] : paths.filter((p) => p !== file.path)));
                              }}
                            />
                          )}
                        </div>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => select(file)}
                          title={file.path}
                          className="flex-1 min-w-0 flex items-center gap-2.5 text-left cursor-pointer"
                        >
                          <FileIcon file={file} size={15} />
                          <span className="truncate text-[13px] text-[#EAEAEA] flex-1">{file.name}</span>
                          <span className="text-[11px] text-[#929292] tabular-nums shrink-0">
                            {file.folder ? 'Папка' : sizeLabel(file.size)}
                          </span>
                        </button>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
                  {rows.map((file) => {
                    const checked = selectedPaths.includes(file.path);
                    const selected = editor?.path === file.path || checked;
                    return (
                      <div
                        key={file.path}
                        className={`relative border bg-[#0D0D0D] transition-colors ${selected ? 'border-[#7F49B4] bg-[#7F49B4]/14' : 'border-[#262626] hover:border-[#454545]'}`}
                      >
                        {!file.folder && !file.path.startsWith('@tasks/') && (
                          <div className="absolute top-2 right-2 z-10">
                            <Checkbox
                              aria-label={`Добавить ${file.name} в архив`}
                              checked={checked}
                              disabled={busy}
                              onCheckedChange={(next) => {
                                setSelectedPaths((paths) => (next ? [...paths, file.path] : paths.filter((p) => p !== file.path)));
                              }}
                            />
                          </div>
                        )}
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => select(file)}
                          onDragOver={(event) => { if (file.folder) event.preventDefault(); }}
                          onDrop={(event) => { if (file.folder) dropFiles(event, file.path); }}
                          title={file.path}
                          className="w-full h-full p-2.5 text-left flex flex-col gap-1.5 cursor-pointer"
                        >
                          <FileIcon file={file} size={20} />
                          <span className="text-[12px] text-[#EAEAEA] break-words line-clamp-2">{file.name}</span>
                          <span className="text-[11px] text-[#929292] mt-auto tabular-nums">
                            {file.folder ? 'Папка' : sizeLabel(file.size)}
                          </span>
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </section>
        )}

        {/* Right File Preview / Editor Panel */}
        <SlidePanel
          open={Boolean(editor)}
          className="min-w-0 border-t xl:border-t-0 xl:border-l border-[#242424] bg-[#0E0E0E] p-3 flex flex-col gap-2.5 lg:col-span-2 xl:col-span-1"
        >
          {editor && (
            <>
              <div className="flex items-start justify-between gap-2 border-b border-[#222222] pb-2">
              <div className="flex items-start gap-2 min-w-0">
                <FileIcon file={editor} size={16} />
                <div className="min-w-0">
                  <div className="text-[13px] font-semibold text-[#EAEAEA] break-words">{editor.name}</div>
                  <div className="text-[11px] text-[#929292] break-all">{editor.path}</div>
                </div>
              </div>
              <IconButton
                size="icon-sm"
                variant="ghost"
                disabled={busy}
                aria-label="Закрыть просмотр"
                onClick={() => {
                  if (!dirty || window.confirm('Закрыть без сохранения изменений?')) setEditor(null);
                }}
              >
                <X className="h-4 w-4" />
              </IconButton>
            </div>

            <div className="flex flex-wrap items-center gap-1.5">
              {editor.url && (
                <Button size="sm" variant="default" leftIcon={<Download className="h-3 w-3" />} asChild>
                  <a href={editor.url} download>Скачать</a>
                </Button>
              )}
              {editor.url && !editor.path.startsWith('@tasks/') && (
                <Button
                  size="sm"
                  variant="danger-ghost"
                  leftIcon={<Trash2 className="h-3 w-3" />}
                  loading={deleteFile.isPending}
                  disabled={busy}
                  onClick={() => {
                    const changes = dirty ? ' Несохранённые изменения будут потеряны.' : '';
                    if (window.confirm(`Удалить файл «${editor.name}» без возможности восстановления?${changes}`)) {
                      deleteFile.mutate(editor);
                    }
                  }}
                >
                  Удалить
                </Button>
              )}
              {editor.editable && (
                <SegmentedControl
                  size="sm"
                  value={mode}
                  onChange={setMode}
                  options={[{ value: 'preview', label: 'Просмотр' }, { value: 'edit', label: 'Редактор' }]}
                />
              )}
              {dirty && <Badge variant="warning">Не сохранено</Badge>}
            </div>

            {open.isPending ? (
              <div className="py-8 flex justify-center"><Spinner label="Чтение файла…" /></div>
            ) : mode === 'edit' && editor.editable ? (
              <div className="flex flex-col gap-2">
                <Textarea
                  aria-label="Содержимое файла"
                  rows={16}
                  className="font-mono text-[12px] leading-relaxed"
                  value={editor.content}
                  onChange={(event) => {
                    const content = event.currentTarget.value;
                    setEditor((curr) => ({ ...curr, content }));
                  }}
                />
                <Button variant="primary" loading={save.isPending} disabled={!dirty || open.isPending} onClick={() => save.mutate(editor)}>
                  Сохранить файл
                </Button>
              </div>
            ) : (
              <div className="flex flex-col gap-2 relative">
                {preview.isFetching && (
                  <div className="flex items-center gap-1.5 text-[11px] text-[#929292]" role="status">
                    <Spinner size={12} label="Загрузка предпросмотра…" />
                  </div>
                )}
                {preview.error && <Notice tone="warning">{preview.error.message}</Notice>}
                {data?.notice && <div className="text-[11px] text-[#929292]">{data.notice}</div>}
                {data?.image && (
                  <img
                    className="block w-full max-h-[600px] object-contain bg-white border border-[#303030]"
                    src={`data:${data.mime ?? 'image/jpeg'};base64,${data.image}`}
                    alt={editor.name}
                  />
                )}
                {data?.pages > 1 && (
                  <div className="flex items-center justify-between gap-2">
                    <Button size="sm" variant="default" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                      <ChevronLeft className="h-3.5 w-3.5" />
                    </Button>
                    <span className="text-[12px] text-[#929292] tabular-nums">Страница {page} из {data.pages}</span>
                    <Button size="sm" variant="default" disabled={page >= data.pages} onClick={() => setPage(page + 1)}>
                      <ChevronRight className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                )}
                {data?.table && (
                  <div className="flex flex-col gap-2">
                    <SelectField
                      size="sm"
                      ariaLabel="Лист таблицы"
                      value={sheet || data.table.sheets[0] || null}
                      options={data.table.sheets}
                      onChange={(value) => {
                        setSheet(value ?? '');
                        setOffset(0);
                      }}
                    />
                    <div className="file-table-scroll">
                      <table className="file-table">
                        <thead>
                          <tr>
                            <th>№</th>
                            {columns.map((column) => <th key={column}>{column}</th>)}
                          </tr>
                        </thead>
                        <tbody>
                          {data.table.rows.map((row) => (
                            <tr key={`${row.sheet}:${row.row}`}>
                              <th>{row.row}</th>
                              {columns.map((column) => <td key={column}>{row.cells[column]}</td>)}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="flex items-center justify-between gap-2">
                      <Button size="sm" variant="default" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 40))}>
                        <ChevronLeft className="h-3.5 w-3.5" />
                      </Button>
                      <span className="text-[12px] text-[#929292] tabular-nums">
                        {offset + 1}–{offset + data.table.rows.length} из {data.table.matched}
                      </span>
                      <Button size="sm" variant="default" disabled={data.table.next_offset == null} onClick={() => setOffset(data.table.next_offset)}>
                        <ChevronRight className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>
                )}
                {data?.text && !data.image && (
                  <pre className="m-0 p-3 bg-[#090909] border border-[#262626] font-mono text-[12px] leading-relaxed whitespace-pre-wrap break-words max-h-[600px] overflow-auto">
                    {data.text}
                  </pre>
                )}
                {!preview.isFetching && !preview.error && !data?.text && !data?.image && !data?.table && (
                  <div className="text-[12px] text-[#929292] py-4">
                    Предпросмотр недоступен. Файл можно скачать.
                  </div>
                )}
              </div>
            )}
            </>
          )}
        </SlidePanel>
      </div>

      {/* Create Archive Modal */}
      <Modal opened={archiveOpen} onClose={() => setArchiveOpen(false)} title="Создать ZIP-архив" size="sm">
        <div className="flex flex-col gap-3">
          <p className="text-[12px] text-[#929292] m-0">
            Выбрано файлов: {selectedPaths.length}. Исходники останутся без изменений.
          </p>
          <Field label="Путь нового архива">
            <Input value={archivePath} onChange={(event) => setArchivePath(event.currentTarget.value)} maxLength={500} autoFocus />
          </Field>
          <div className="flex justify-end gap-2">
            <Button variant="default" onClick={() => setArchiveOpen(false)}>Отмена</Button>
            <Button
              variant="primary"
              loading={archive.isPending}
              disabled={selectedPaths.length < 2 || !archivePath.trim()}
              onClick={() => archive.mutate({ paths: selectedPaths, output_path: archivePath.trim() })}
            >
              Создать архив
            </Button>
          </div>
        </div>
      </Modal>

      {/* Create Folder / File Modal */}
      <Modal opened={Boolean(creating)} onClose={() => setCreating(null)} title={creating === 'folder' ? 'Новая папка' : 'Новый файл'} size="sm">
        <div className="flex flex-col gap-3">
          <div className="text-[12px] text-[#929292]">/{folder || 'Мои файлы'}</div>
          <Field label="Название">
            <Input
              placeholder={creating === 'folder' ? 'Например: Учёба' : 'Например: заметки.md'}
              value={name}
              onChange={(event) => setName(event.currentTarget.value)}
              maxLength={200}
              autoFocus
            />
          </Field>
          <div className="flex justify-end gap-2">
            <Button variant="default" onClick={() => setCreating(null)}>Отмена</Button>
            <Button
              variant="primary"
              loading={createFolder.isPending}
              disabled={!name.trim() || name.includes('/') || name.includes('\\')}
              onClick={create}
            >
              Создать
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
