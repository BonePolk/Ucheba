import { Children, Fragment, cloneElement, isValidElement, useEffect, useRef } from 'react';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import Markdown from 'react-markdown';
import {
  ChevronLeft,
  ChevronRight,
  Download,
  File,
  FileSpreadsheet,
  FileText,
  Folder,
  Image as ImageIcon,
  Save,
  Trash2,
  X,
} from 'lucide-react';
import { EASE_OUT, motion, useReducedMotion } from '@/lib/motion';
import { Button, IconButton } from '@/components/ui/button';
import { Textarea } from '@/components/ui/input';
import { SelectField } from '@/components/ui/select';
import { Badge, Notice, Spinner } from '@/components/ui/surface';
import { SegmentedControl } from '@/components/ui/tabs';

const INSTANT_TRANSITION = { duration: 0.01 };

function extensionOf(file) {
  return file?.name?.split('.').at(-1)?.toLowerCase() ?? '';
}

export function isMarkdownFile(file) {
  return ['md', 'markdown', 'mdx'].includes(extensionOf(file));
}

export function isDocxFile(file) {
  return extensionOf(file) === 'docx';
}

export function fileType(file) {
  if (file?.folder) return 'folder';
  const ext = extensionOf(file);
  if (['md', 'markdown', 'mdx'].includes(ext)) return 'markdown';
  if (['docx', 'doc'].includes(ext)) return 'word';
  if (['xlsx', 'xls', 'ods', 'csv', 'tsv'].includes(ext)) return 'table';
  if (['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'].includes(ext)) return 'image';
  if (ext === 'pdf') return 'pdf';
  return 'document';
}

export function FileIcon({ file, size = 16 }) {
  const type = fileType(file);
  const props = { style: { width: size, height: size }, 'aria-hidden': true };
  if (type === 'folder') return <Folder {...props} className="shrink-0 text-[#CFA9F5]" />;
  if (type === 'markdown') return <FileText {...props} className="shrink-0 text-[#B98CE8]" />;
  if (type === 'word') return <FileText {...props} className="shrink-0 text-[#6FA3CC]" />;
  if (type === 'table') return <FileSpreadsheet {...props} className="shrink-0 text-[#6EBE86]" />;
  if (type === 'image') return <ImageIcon {...props} className="shrink-0 text-[#6FA3CC]" />;
  if (type === 'pdf') return <FileText {...props} className="shrink-0 text-[#D46A6A]" />;
  return <File {...props} className="shrink-0 text-[#929292]" />;
}

export function sizeLabel(size) {
  if (size == null) return '—';
  if (size < 1024) return `${size} Б`;
  return size < 1024 * 1024 ? `${(size / 1024).toFixed(1)} КБ` : `${(size / 1024 / 1024).toFixed(1)} МБ`;
}

function clearStuckBodyPointerLock() {
  if (typeof document === 'undefined') return;
  if (!document.querySelector('[role="dialog"][data-state="open"], [role="menu"][data-state="open"], [role="listbox"][data-state="open"]')) {
    if (document.body.style.pointerEvents === 'none') {
      document.body.style.pointerEvents = '';
    }
  }
}

function isSafeHref(href) {
  if (!href || typeof href !== 'string') return false;
  const trimmed = href.trim();
  return /^(https?:|mailto:|#)/i.test(trimmed);
}

function splitPipeRow(line) {
  const cells = [];
  let current = '';
  let escaped = false;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (escaped) {
      current += ch === '|' ? '|' : `\\${ch}`;
      escaped = false;
      continue;
    }
    if (ch === '\\') {
      escaped = true;
      continue;
    }
    if (ch === '|') {
      cells.push(current.trim());
      current = '';
      continue;
    }
    current += ch;
  }
  if (escaped) current += '\\';
  cells.push(current.trim());

  const trimmedLine = line.trim();
  if (trimmedLine.startsWith('|') && cells.length > 0 && cells[0] === '') cells.shift();
  if (trimmedLine.endsWith('|') && !trimmedLine.endsWith('\\|') && cells.length > 0 && cells[cells.length - 1] === '') cells.pop();
  return cells;
}

function parseDelimiterRow(line) {
  if (!line || !line.includes('|')) return null;
  const cells = splitPipeRow(line);
  if (!cells.length) return null;
  const alignments = [];
  for (const cell of cells) {
    if (!/^:?-{1,}:?$/.test(cell)) return null;
    if (cell.startsWith(':') && cell.endsWith(':')) alignments.push('center');
    else if (cell.endsWith(':')) alignments.push('right');
    else if (cell.startsWith(':')) alignments.push('left');
    else alignments.push(undefined);
  }
  return alignments;
}

function splitMarkdownSegments(text) {
  const lines = String(text ?? '').replace(/\r\n?/g, '\n').split('\n');
  const segments = [];
  let mdBuffer = [];
  let fence = null;

  const flushMd = () => {
    if (!mdBuffer.length) return;
    segments.push({ type: 'md', text: mdBuffer.join('\n') });
    mdBuffer = [];
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const trimmed = line.trimStart();

    if (fence) {
      mdBuffer.push(line);
      const closeRe = new RegExp(`^${fence.char}{${fence.len},}\\s*$`);
      if (closeRe.test(trimmed)) fence = null;
      continue;
    }

    const fenceMatch = trimmed.match(/^(`{3,}|~{3,})/);
    if (fenceMatch) {
      fence = { char: fenceMatch[1][0], len: fenceMatch[1].length };
      mdBuffer.push(line);
      continue;
    }

    if (line.includes('|') && i + 1 < lines.length) {
      const alignments = parseDelimiterRow(lines[i + 1]);
      if (alignments) {
        const headers = splitPipeRow(line);
        if (headers.length > 0) {
          flushMd();
          const colCount = alignments.length;
          const normHeaders = Array.from({ length: colCount }, (_, idx) => headers[idx] ?? '');
          const rows = [];
          let j = i + 2;
          while (j < lines.length) {
            const rowLine = lines[j];
            const rowTrimmed = rowLine.trim();
            if (!rowTrimmed || !rowLine.includes('|')) break;
            if (/^(`{3,}|~{3,})/.test(rowTrimmed) || /^#{1,6}\s/.test(rowTrimmed)) break;
            const cells = splitPipeRow(rowLine);
            rows.push(Array.from({ length: colCount }, (_, idx) => cells[idx] ?? ''));
            j += 1;
          }
          segments.push({ type: 'table', headers: normHeaders, alignments, rows });
          i = j - 1;
          continue;
        }
      }
    }

    mdBuffer.push(line);
  }

  flushMd();
  return segments;
}

function extractTaskCheckbox(children) {
  const arr = Children.toArray(children);
  if (!arr.length) return null;

  const matchText = (value) => {
    if (typeof value !== 'string') return null;
    const match = value.match(/^\[( |x|X)\]\s*(.*)$/s);
    if (!match) return null;
    return { checked: match[1].toLowerCase() === 'x', rest: match[2] };
  };

  const firstDirect = matchText(arr[0]);
  if (firstDirect) {
    const rest = firstDirect.rest ? [firstDirect.rest, ...arr.slice(1)] : arr.slice(1);
    return { checked: firstDirect.checked, content: rest };
  }

  if (isValidElement(arr[0]) && arr[0].type === 'p') {
    const pChildren = Children.toArray(arr[0].props.children);
    const firstNested = matchText(pChildren[0]);
    if (firstNested) {
      const updatedPChildren = firstNested.rest ? [firstNested.rest, ...pChildren.slice(1)] : pChildren.slice(1);
      const updatedP = cloneElement(arr[0], undefined, ...updatedPChildren);
      return { checked: firstNested.checked, content: [updatedP, ...arr.slice(1)] };
    }
  }

  return null;
}

const inlineComponents = {
  p: ({ children }) => <>{children}</>,
  a: ({ href, children }) => (
    isSafeHref(href)
      ? <a href={href} target="_blank" rel="noreferrer">{children}</a>
      : <span>{children}</span>
  ),
  img: ({ src, alt }) => (
    isSafeHref(src)
      ? <a href={src} target="_blank" rel="noreferrer">{alt || 'Открыть изображение'}</a>
      : <span>{alt || 'Изображение'}</span>
  ),
};

const mdComponents = {
  a: inlineComponents.a,
  img: inlineComponents.img,
  li: ({ children }) => {
    const task = extractTaskCheckbox(children);
    if (!task) return <li>{children}</li>;
    return (
      <li className="glance-task-item">
        <span aria-hidden="true" className={task.checked ? 'glance-task-box is-checked' : 'glance-task-box'}>
          {task.checked ? '✓' : ''}
        </span>
        <span className="min-w-0 flex-1">{task.content}</span>
      </li>
    );
  },
};

function MarkdownDocument({ content }) {
  const segments = splitMarkdownSegments(content);
  return (
    <article className="glance-markdown">
      {segments.map((seg, idx) => {
        if (seg.type === 'md') {
          return (
            <Markdown key={idx} skipHtml components={mdComponents}>
              {seg.text}
            </Markdown>
          );
        }
        return (
          <div key={idx} className="glance-table-wrap">
            <table>
              <thead>
                <tr>
                  {seg.headers.map((header, colIdx) => (
                    <th key={colIdx} style={seg.alignments[colIdx] ? { textAlign: seg.alignments[colIdx] } : undefined}>
                      <Markdown skipHtml components={inlineComponents}>{header}</Markdown>
                    </th>
                  ))}
                </tr>
              </thead>
              {seg.rows.length > 0 && (
                <tbody>
                  {seg.rows.map((row, rowIdx) => (
                    <tr key={rowIdx}>
                      {row.map((cell, colIdx) => (
                        <td key={colIdx} style={seg.alignments[colIdx] ? { textAlign: seg.alignments[colIdx] } : undefined}>
                          <Markdown skipHtml components={inlineComponents}>{cell}</Markdown>
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              )}
            </table>
          </div>
        );
      })}
    </article>
  );
}

function DocxRuns({ runs = [] }) {
  return runs.map((run, idx) => {
    if (run.br) return <br key={idx} />;
    if (run.image) {
      return (
        <img
          key={idx}
          className="glance-inline-img"
          src={`data:${run.image.mime};base64,${run.image.data}`}
          alt={run.image.alt || ''}
        />
      );
    }
    let node = run.text ?? '';
    if (run.code) node = <code>{node}</code>;
    if (run.strike) node = <s>{node}</s>;
    if (run.underline) node = <u>{node}</u>;
    if (run.italic) node = <em>{node}</em>;
    if (run.bold) node = <strong>{node}</strong>;
    if (run.sup) node = <sup>{node}</sup>;
    if (run.sub) node = <sub>{node}</sub>;
    if (run.href && /^https?:\/\//i.test(run.href)) {
      node = <a href={run.href} target="_blank" rel="noreferrer">{node}</a>;
    }
    return <Fragment key={idx}>{node}</Fragment>;
  });
}

function DocxTable({ rows = [] }) {
  let leadingHeaders = 0;
  for (const row of rows) {
    if (!row.header) break;
    leadingHeaders += 1;
  }
  const canSplitThead = leadingHeaders > 0 && leadingHeaders < rows.length && rows
    .slice(0, leadingHeaders)
    .every((row, idx) => row.cells.every((cell) => (cell.rowSpan ?? 1) <= leadingHeaders - idx));

  const theadRows = canSplitThead ? rows.slice(0, leadingHeaders) : [];
  const tbodyRows = canSplitThead ? rows.slice(leadingHeaders) : rows;

  const renderRow = (row, rowIdx, forceTd = false) => (
    <tr key={rowIdx}>
      {row.cells.map((cell, cellIdx) => {
        const Tag = !forceTd && row.header ? 'th' : 'td';
        return (
          <Tag
            key={cellIdx}
            colSpan={cell.colSpan > 1 ? cell.colSpan : undefined}
            rowSpan={cell.rowSpan > 1 ? cell.rowSpan : undefined}
          >
            <DocxBlocks blocks={cell.blocks ?? []} compact />
          </Tag>
        );
      })}
    </tr>
  );

  return (
    <div className="glance-table-wrap">
      <table>
        {theadRows.length > 0 && <thead>{theadRows.map((row, idx) => renderRow(row, idx))}</thead>}
        <tbody>{tbodyRows.map((row, idx) => renderRow(row, idx))}</tbody>
      </table>
    </div>
  );
}

function DocxBlocks({ blocks = [], compact = false }) {
  return blocks.map((block, idx) => {
    if (block.type === 'heading') {
      const level = Math.max(1, Math.min(Number(block.level) || 1, 6));
      const Tag = `h${level}`;
      return (
        <Tag key={idx} style={block.align ? { textAlign: block.align } : undefined}>
          <DocxRuns runs={block.runs} />
        </Tag>
      );
    }
    if (block.type === 'paragraph') {
      if (!block.runs?.length) {
        return compact ? null : <p key={idx} className="glance-empty-p" aria-hidden="true" />;
      }
      return (
        <p key={idx} style={block.align ? { textAlign: block.align } : undefined}>
          <DocxRuns runs={block.runs} />
        </p>
      );
    }
    if (block.type === 'list') {
      const ListTag = block.ordered ? 'ol' : 'ul';
      return (
        <ListTag key={idx}>
          {(block.items ?? []).map((item, itemIdx) => (
            <li
              key={itemIdx}
              style={item.level > 0 ? { marginLeft: `${item.level * 18}px` } : undefined}
            >
              <DocxRuns runs={item.runs} />
            </li>
          ))}
        </ListTag>
      );
    }
    if (block.type === 'table') {
      return <DocxTable key={idx} rows={block.rows} />;
    }
    if (block.type === 'image') {
      return (
        <figure key={idx} className="glance-docx-figure">
          <img src={`data:${block.mime};base64,${block.data}`} alt={block.alt || ''} />
        </figure>
      );
    }
    if (block.type === 'page_break') {
      return (
        <div key={idx} className="glance-page-break">
          <span>Разрыв страницы</span>
        </div>
      );
    }
    return null;
  });
}

function DocxDocument({ document }) {
  return (
    <div className="flex flex-col gap-4">
      <article className="glance-docx">
        <DocxBlocks blocks={document?.blocks ?? []} />
      </article>
      {document?.truncated && (
        <Notice tone="info">Показана часть документа. Скачайте файл, чтобы открыть целиком.</Notice>
      )}
    </div>
  );
}

export default function FileGlance({
  editor,
  onClose,
  mode,
  onModeChange,
  dirty,
  busy,
  opening,
  saving,
  deleting,
  onChangeContent,
  onSave,
  onDelete,
  preview,
  page,
  onPageChange,
  sheet,
  onSheetChange,
  offset,
  onOffsetChange,
  columns,
  fileIndex = -1,
  fileCount = 0,
  onNavigate,
}) {
  const open = Boolean(editor);
  const reduced = useReducedMotion();
  const scrollRef = useRef(null);

  const data = preview?.data;
  const isTable = editor ? fileType(editor) === 'table' : false;
  const localText = editor?.editable && !isTable && editor.content !== undefined ? editor.content : null;
  const hasPrev = fileIndex > 0;
  const hasNext = fileIndex >= 0 && fileIndex < fileCount - 1;

  useEffect(() => {
    if (open) {
      return () => {
        requestAnimationFrame(clearStuckBodyPointerLock);
      };
    }
    const id = requestAnimationFrame(clearStuckBodyPointerLock);
    return () => cancelAnimationFrame(id);
  }, [open]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 });
  }, [editor?.path]);

  useEffect(() => {
    if (!open) return undefined;
    function onKeyDown(event) {
      if (event.defaultPrevented) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        if (mode === 'edit' && editor?.editable) {
          event.preventDefault();
          if (dirty && !saving && !opening) onSave?.();
        }
        return;
      }
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const tag = event.target?.tagName;
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) || event.target?.isContentEditable) return;
      if (document.querySelector('[role="listbox"][data-state="open"], [role="menu"][data-state="open"]')) return;
      if (mode === 'edit') return;

      if (event.key === 'ArrowLeft' && hasPrev && !busy) {
        event.preventDefault();
        onNavigate?.(-1);
      } else if (event.key === 'ArrowRight' && hasNext && !busy) {
        event.preventDefault();
        onNavigate?.(1);
      } else if (event.key === 'PageUp' && data?.pages > 1 && page > 1) {
        event.preventDefault();
        onPageChange?.(page - 1);
      } else if (event.key === 'PageDown' && data?.pages > 1 && page < data.pages) {
        event.preventDefault();
        onPageChange?.(page + 1);
      }
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open, mode, editor?.editable, dirty, saving, opening, hasPrev, hasNext, busy, data?.pages, page, onSave, onNavigate, onPageChange]);

  const markdownText = editor && isMarkdownFile(editor) ? (localText ?? data?.text ?? null) : null;
  const docxDoc = data?.document;
  const hasDocxBlocks = Boolean(docxDoc?.blocks?.length);
  const plainText = !isMarkdownFile(editor) && !data?.image && !data?.table && !docxDoc ? (localText ?? data?.text ?? null) : null;

  return (
    <DialogPrimitive.Root open={open} onOpenChange={(next) => { if (!next) onClose?.(); }}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay asChild>
          <motion.div
            initial={reduced ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={reduced ? INSTANT_TRANSITION : { duration: 0.14, ease: EASE_OUT }}
            className="fixed inset-0 z-50 bg-black/80"
          />
        </DialogPrimitive.Overlay>
        <DialogPrimitive.Content asChild aria-describedby={undefined}>
          <motion.div
            initial={reduced ? false : { opacity: 0, scale: 0.992 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={reduced ? INSTANT_TRANSITION : { duration: 0.16, ease: EASE_OUT }}
            className="fixed inset-0 z-50 bg-[#0A0A0A]/96 backdrop-blur-sm flex flex-col text-[#EAEAEA] focus:outline-none"
          >
            {editor && (
              <>
                <header className="shrink-0 border-b border-[#242424] bg-[#111111]/95 px-3 sm:px-5 py-2 flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <FileIcon file={editor} size={17} />
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 min-w-0">
                        <DialogPrimitive.Title className="text-[14px] font-semibold text-[#EAEAEA] m-0 truncate">
                          {editor.name}
                        </DialogPrimitive.Title>
                        {dirty && <Badge variant="warning">Не сохранено</Badge>}
                      </div>
                      <div className="text-[11px] text-[#929292] truncate">
                        {editor.path}{editor.size != null ? ` · ${sizeLabel(editor.size)}` : ''}
                      </div>
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-1.5 shrink-0">
                    {fileCount > 1 && fileIndex >= 0 && (
                      <div className="inline-flex items-center gap-1 mr-1">
                        <IconButton
                          size="icon-sm"
                          variant="default"
                          disabled={busy || !hasPrev}
                          aria-label="Предыдущий файл"
                          onClick={() => onNavigate?.(-1)}
                        >
                          <ChevronLeft className="h-3.5 w-3.5" />
                        </IconButton>
                        <span className="text-[11px] text-[#929292] tabular-nums px-1">
                          {fileIndex + 1} / {fileCount}
                        </span>
                        <IconButton
                          size="icon-sm"
                          variant="default"
                          disabled={busy || !hasNext}
                          aria-label="Следующий файл"
                          onClick={() => onNavigate?.(1)}
                        >
                          <ChevronRight className="h-3.5 w-3.5" />
                        </IconButton>
                      </div>
                    )}

                    {editor.editable && (
                      <SegmentedControl
                        size="sm"
                        ariaLabel="Режим файла"
                        value={mode}
                        onChange={onModeChange}
                        options={[{ value: 'preview', label: 'Просмотр' }, { value: 'edit', label: 'Редактор' }]}
                      />
                    )}

                    {mode === 'edit' && editor.editable && (
                      <Button
                        size="sm"
                        variant="primary"
                        leftIcon={<Save className="h-3 w-3" />}
                        loading={saving}
                        disabled={!dirty || opening}
                        onClick={onSave}
                      >
                        Сохранить
                      </Button>
                    )}

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
                        loading={deleting}
                        disabled={busy}
                        onClick={onDelete}
                      >
                        Удалить
                      </Button>
                    )}

                    <IconButton
                      size="icon-sm"
                      variant="ghost"
                      disabled={busy}
                      aria-label="Закрыть просмотр"
                      onClick={onClose}
                    >
                      <X className="h-4 w-4" />
                    </IconButton>
                  </div>
                </header>

                <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto">
                  {opening ? (
                    <div className="h-full min-h-[240px] flex items-center justify-center">
                      <Spinner label="Чтение файла…" />
                    </div>
                  ) : mode === 'edit' && editor.editable ? (
                    <div className="max-w-[1000px] mx-auto h-full p-3 sm:p-5 flex flex-col gap-2">
                      <Textarea
                        aria-label="Содержимое файла"
                        rows={18}
                        className="flex-1 min-h-[300px] resize-none font-mono text-[13px] leading-relaxed p-3 bg-[#0D0D0D]"
                        value={editor.content ?? ''}
                        onChange={(event) => onChangeContent?.(event.currentTarget.value)}
                      />
                    </div>
                  ) : preview?.isFetching && !data && localText == null ? (
                    <div className="h-full min-h-[240px] flex items-center justify-center">
                      <Spinner label="Загрузка предпросмотра…" />
                    </div>
                  ) : (
                    <div className={data?.table ? 'h-full flex flex-col gap-2.5 p-3 sm:p-5' : data?.image ? 'min-h-full flex flex-col items-center justify-center p-3 sm:p-6 gap-3' : 'p-3 sm:p-5'}>
                      {preview?.isFetching && (
                        <div className="max-w-[880px] mx-auto mb-2 flex items-center gap-1.5 text-[11px] text-[#929292]" role="status">
                          <Spinner size={12} label="Загрузка предпросмотра…" />
                        </div>
                      )}

                      {preview?.error && (
                        <div className="max-w-[840px] mx-auto w-full mb-3">
                          <Notice tone="warning">{preview.error.message}</Notice>
                        </div>
                      )}

                      {data?.notice && !data?.table && (
                        <div className="max-w-[880px] mx-auto w-full mb-2 text-[11px] text-[#929292]">{data.notice}</div>
                      )}

                      {markdownText != null && (
                        <div className="max-w-[840px] mx-auto my-2 sm:my-4 px-5 sm:px-9 py-6 sm:py-8 border border-[#262626] bg-[#111111]">
                          {markdownText.trim() ? (
                            <MarkdownDocument content={markdownText} />
                          ) : (
                            <div className="text-[12px] text-[#929292]">Файл пуст</div>
                          )}
                        </div>
                      )}

                      {docxDoc && (
                        <div className="max-w-[880px] mx-auto my-2 sm:my-4 px-5 sm:px-10 py-6 sm:py-9 border border-[#262626] bg-[#111111]">
                          {hasDocxBlocks ? (
                            <DocxDocument document={docxDoc} />
                          ) : data?.text ? (
                            <pre className="m-0 font-mono text-[13px] leading-relaxed whitespace-pre-wrap break-words">{data.text}</pre>
                          ) : (
                            <div className="text-[12px] text-[#929292]">Документ пуст</div>
                          )}
                        </div>
                      )}

                      {data?.image && (
                        <>
                          <img
                            className="block max-w-full max-h-[calc(100dvh-132px)] object-contain bg-white border border-[#303030]"
                            src={`data:${data.mime ?? 'image/jpeg'};base64,${data.image}`}
                            alt={editor.name}
                          />
                          {data.pages > 1 && (
                            <div className="inline-flex items-center gap-3 border border-[#303030] bg-[#111111] px-2.5 py-1">
                              <Button size="sm" variant="default" disabled={page <= 1} onClick={() => onPageChange?.(page - 1)}>
                                <ChevronLeft className="h-3.5 w-3.5" />
                              </Button>
                              <span className="text-[12px] text-[#929292] tabular-nums">Страница {page} из {data.pages}</span>
                              <Button size="sm" variant="default" disabled={page >= data.pages} onClick={() => onPageChange?.(page + 1)}>
                                <ChevronRight className="h-3.5 w-3.5" />
                              </Button>
                            </div>
                          )}
                        </>
                      )}

                      {data?.table && (
                        <>
                          <div className="flex flex-wrap items-center justify-between gap-2 shrink-0">
                            <SelectField
                              size="sm"
                              ariaLabel="Лист таблицы"
                              className="w-[240px] max-w-full"
                              value={sheet || data.table.sheets[0] || null}
                              options={data.table.sheets}
                              onChange={(value) => {
                                onSheetChange?.(value ?? '');
                                onOffsetChange?.(0);
                              }}
                            />
                            {data.notice && <span className="text-[11px] text-[#929292]">{data.notice}</span>}
                          </div>
                          <div className="file-table-scroll glance-table-scroll">
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
                          <div className="flex items-center justify-between gap-2 shrink-0">
                            <Button size="sm" variant="default" disabled={!offset} onClick={() => onOffsetChange?.(Math.max(0, offset - 40))}>
                              <ChevronLeft className="h-3.5 w-3.5" />
                            </Button>
                            <span className="text-[12px] text-[#929292] tabular-nums">
                              {offset + 1}–{offset + data.table.rows.length} из {data.table.matched}
                            </span>
                            <Button size="sm" variant="default" disabled={data.table.next_offset == null} onClick={() => onOffsetChange?.(data.table.next_offset)}>
                              <ChevronRight className="h-3.5 w-3.5" />
                            </Button>
                          </div>
                        </>
                      )}

                      {plainText != null && (
                        <div className="max-w-[1040px] mx-auto my-2 px-4 sm:px-5 py-4 border border-[#262626] bg-[#0D0D0D]">
                          {plainText ? (
                            <pre className="m-0 font-mono text-[13px] leading-relaxed whitespace-pre-wrap break-words">{plainText}</pre>
                          ) : (
                            <div className="text-[12px] text-[#929292]">Файл пуст</div>
                          )}
                        </div>
                      )}

                      {!preview?.isFetching && !preview?.error && markdownText == null && !docxDoc && !data?.image && !data?.table && plainText == null && (
                        <div className="max-w-[420px] mx-auto my-12 border border-[#262626] bg-[#111111] p-5 text-center flex flex-col items-center gap-3">
                          <div className="text-[13px] text-[#929292]">
                            Предпросмотр недоступен. Файл можно скачать.
                          </div>
                          {editor.url && (
                            <Button size="sm" variant="primary" leftIcon={<Download className="h-3.5 w-3.5" />} asChild>
                              <a href={editor.url} download>Скачать</a>
                            </Button>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </>
            )}
          </motion.div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
