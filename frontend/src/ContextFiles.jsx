import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, FolderOpen, Paperclip, Trash2 } from 'lucide-react';
import { request } from './api';
import { fileContent } from './fileUpload';
import { Button, IconButton } from '@/components/ui/button';
import { FileTrigger } from '@/components/ui/controls';
import { Combobox } from '@/components/ui/select';
import { Notice } from '@/components/ui/surface';
import { AnimatePresence, CollapseSection, FAST_TRANSITION, motion } from '@/lib/motion';

export default function ContextFiles({ taskId, disabled }) {
  const client = useQueryClient();
  const reset = useRef(null);
  const [saved, setSaved] = useState(null);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const path = `/integrations/tasks/${taskId}/monocle/files`;
  const files = useQuery({ queryKey: ['context-files', taskId], queryFn: () => request(path) });
  const library = useQuery({ queryKey: ['context-file-library'], queryFn: () => request('/integrations/monocle/files') });

  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ['context-files', taskId] });
    await client.invalidateQueries({ queryKey: ['context-file-library'] });
    await client.invalidateQueries({ queryKey: ['context', taskId] });
    await client.invalidateQueries({ queryKey: ['tasks'] });
  };

  const upload = useMutation({
    mutationFn: async (file) => {
      if (file.size > 10 * 1024 * 1024) throw new Error('Максимальный размер файла — 10 МБ');
      return request(path, { method: 'POST', body: JSON.stringify({ name: file.name, content: await fileContent(file) }) });
    },
    onSuccess: refresh,
    onSettled: () => reset.current?.(),
  });

  const remove = useMutation({
    mutationFn: (id) => request(`${path}/${id}`, { method: 'DELETE' }),
    onSuccess: refresh,
  });

  const reuse = useMutation({
    mutationFn: () => {
      const [source_task_id, file_id] = saved.split(':');
      return request(`${path}/reuse`, {
        method: 'POST',
        body: JSON.stringify({ source_task_id: Number(source_task_id), file_id }),
      });
    },
    onSuccess: async () => {
      setSaved(null);
      setLibraryOpen(false);
      await refresh();
    },
  });

  const busy = disabled || upload.isPending || remove.isPending || reuse.isPending;
  const error = files.error || library.error || upload.error || remove.error || reuse.error;
  const libraryOptions = (library.data ?? [])
    .filter((file) => file.task_id !== taskId || !files.data?.some((attached) => attached.id === file.id))
    .map((file) => ({ value: `${file.task_id}:${file.id}`, label: `${file.name} · #${file.task_id}` }));

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-[11px] text-[#929292] tabular-nums">
          {files.data?.length ?? 0}/5 · макс. 10 МБ
        </span>
        <div className="flex items-center gap-1.5">
          {libraryOptions.length > 0 && (
            <Button
              size="sm"
              variant={libraryOpen ? 'accent-subtle' : 'outline'}
              leftIcon={<FolderOpen className="h-3.5 w-3.5" />}
              disabled={busy || files.data?.length >= 5}
              onClick={() => setLibraryOpen(!libraryOpen)}
              aria-expanded={libraryOpen}
            >
              Из библиотеки
            </Button>
          )}
          <FileTrigger
            resetRef={reset}
            onChange={(file) => { if (file) upload.mutate(file); }}
            accept=".pdf,.docx,.xlsx,.ods,.pptx,.txt,.md,.csv,.json,.py,.js,.c,.cpp,.h,.java,.sql,.png,.jpg,.jpeg,.webp"
          >
            {(props) => (
              <Button
                {...props}
                size="sm"
                variant="default"
                leftIcon={<Paperclip className="h-3.5 w-3.5" />}
                loading={upload.isPending}
                disabled={busy || files.isPending || files.data?.length >= 5}
              >
                Прикрепить
              </Button>
            )}
          </FileTrigger>
        </div>
      </div>

      {libraryOptions.length > 0 && (
        <CollapseSection open={libraryOpen}>
          <div className="flex flex-wrap items-center gap-2 border border-[#282828] bg-[#0D0D0D] p-2">
            <Combobox
              ariaLabel="Файл из библиотеки"
              placeholder="Выбрать файл из библиотеки…"
              searchPlaceholder="Поиск…"
              clearable
              value={saved}
              onChange={setSaved}
              disabled={busy || library.isPending}
              options={libraryOptions}
              className="flex-1 min-w-[180px]"
            />
            <Button
              size="sm"
              variant="accent-subtle"
              loading={reuse.isPending}
              disabled={busy || !saved || files.data?.length >= 5}
              onClick={() => reuse.mutate()}
            >
              Добавить
            </Button>
          </div>
        </CollapseSection>
      )}

      {files.data?.length > 0 && (
        <div className="border border-[#282828] divide-y divide-[#222222] bg-[#0D0D0D]">
          <AnimatePresence initial={false}>
            {files.data.map((file) => (
              <motion.div
                key={file.id}
                layout="position"
                initial={{ opacity: 0, y: 3 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={FAST_TRANSITION}
                className="flex items-center justify-between gap-2 px-2.5 py-1.5 text-[12px]"
              >
                <a
                  href={`/api${path}/${file.id}`}
                  download
                  className="flex items-center gap-1.5 min-w-0 text-[#CFA9F5] hover:underline truncate"
                >
                  <Download className="h-3.5 w-3.5 shrink-0" />
                  <span className="truncate">{file.name}</span>
                  {file.pages && <span className="text-[#929292] shrink-0">· {file.pages} стр.</span>}
                </a>
                <IconButton
                  size="icon-sm"
                  variant="danger-ghost"
                  aria-label={`Убрать файл ${file.name} из задачи`}
                  title="Убрать из задачи"
                  disabled={busy}
                  onClick={() => remove.mutate(file.id)}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </IconButton>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}

      {error && <Notice tone="error">{error.message}</Notice>}
    </div>
  );
}
