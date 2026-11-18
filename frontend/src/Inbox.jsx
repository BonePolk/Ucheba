import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc';
import { Check, ExternalLink, Inbox as InboxIcon, Pencil, X } from 'lucide-react';
import { getTasks, request } from './api';
import TaskForm from './TaskForm';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/input';
import { Combobox } from '@/components/ui/select';
import { Badge, EmptyState, Notice, PageHeader, PageLoader } from '@/components/ui/surface';
import { AnimatePresence, ExpandableText, LAYOUT_TRANSITION, motion, useReducedMotion } from '@/lib/motion';

dayjs.extend(utc);

const actionMeta = {
  create: { label: 'Новая', variant: 'accent' },
  update: { label: 'Изменение', variant: 'warning' },
  cancel: { label: 'Отмена', variant: 'danger' },
};

export default function Inbox() {
  const client = useQueryClient();
  const reduced = useReducedMotion();
  const [editing, setEditing] = useState(null);
  const [targets, setTargets] = useState({});
  const query = useQuery({
    queryKey: ['inbox'],
    queryFn: () => request('/inbox'),
    refetchInterval: 30000,
    retry: false,
  });
  const tasks = useQuery({ queryKey: ['tasks', 'inbox-targets'], queryFn: () => getTasks() });

  const decision = useMutation({
    mutationFn: ({ id, action, values, target_task_id }) => request(`/inbox/${id}/${action}`, {
      method: 'POST',
      body: JSON.stringify({ values, target_task_id }),
    }),
    onSuccess: async () => {
      setEditing(null);
      await client.invalidateQueries({ queryKey: ['inbox'] });
      await client.invalidateQueries({ queryKey: ['tasks'] });
    },
  });

  const accept = (item, values) => decision.mutate({
    id: item.id,
    action: 'accept',
    values,
    target_task_id: targets[item.id] ? Number(targets[item.id]) : item.target_task_id,
  });

  const items = query.data ?? [];

  return (
    <div className="flex flex-col gap-3">
      <PageHeader title="Входящие" count={items.length} />

      {(query.error || decision.error) && <Notice tone="error">{(query.error || decision.error).message}</Notice>}

      {query.isPending ? (
        <PageLoader />
      ) : items.length === 0 ? (
        <EmptyState icon={<InboxIcon className="h-5 w-5" />} title="Предложений нет" />
      ) : (
        <div className="border border-[#303030] bg-[#111111] divide-y divide-[#222222]">
          <AnimatePresence initial={false}>
            {items.map((item) => {
              const meta = actionMeta[item.action] ?? { label: item.action, variant: 'default' };
              return (
                <motion.div
                  key={item.id}
                  layout={reduced ? false : 'position'}
                  initial={reduced ? false : { opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  transition={reduced ? { duration: 0.01 } : LAYOUT_TRANSITION}
                  className="p-3 flex flex-col gap-2"
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0 flex-1 flex flex-col gap-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant={meta.variant}>{meta.label}</Badge>
                        <span className="text-[14px] font-semibold text-[#EAEAEA] break-words">{item.title}</span>
                      </div>
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-[#929292]">
                        {item.deadline && (
                          <span className="tabular-nums">
                            Срок: {dayjs.utc(item.deadline).local().format('DD.MM.YYYY HH:mm')}
                          </span>
                        )}
                        {item.source_title && <span>{item.source_title}</span>}
                        {item.assignee && <span>{item.assignee}</span>}
                        {item.source_url && (
                          <a href={item.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[#CFA9F5] hover:underline">
                            Telegram <ExternalLink className="h-3 w-3" />
                          </a>
                        )}
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-1.5 shrink-0">
                      <Button size="sm" variant="primary" leftIcon={<Check className="h-3 w-3" />} disabled={decision.isPending} onClick={() => accept(item)}>
                        Принять
                      </Button>
                      {item.action !== 'cancel' && (
                        <Button size="sm" variant="default" leftIcon={<Pencil className="h-3 w-3" />} onClick={() => { decision.reset(); setEditing(item); }}>
                          Изменить
                        </Button>
                      )}
                      <Button size="sm" variant="danger-ghost" leftIcon={<X className="h-3 w-3" />} disabled={decision.isPending} onClick={() => decision.mutate({ id: item.id, action: 'reject' })}>
                        Отклонить
                      </Button>
                    </div>
                  </div>

                  {item.description && (
                    <ExpandableText text={item.description} threshold={240} collapsedHeight="max-h-[110px]">
                      <div className="text-[12px] text-[#D0D0D0] whitespace-pre-wrap break-words border-l border-[#303030] pl-2.5">
                        {item.description}
                      </div>
                    </ExpandableText>
                  )}

                  {item.reason && (
                    <div className="text-[12px] text-[#929292]">{item.reason}</div>
                  )}

                  {item.action !== 'create' && (
                    <Field label="Задача" className="max-w-md">
                      <Combobox
                        clearable
                        placeholder="Выберите задачу…"
                        searchPlaceholder="Поиск задачи…"
                        value={targets[item.id] ?? (item.target_task_id ? String(item.target_task_id) : null)}
                        onChange={(value) => setTargets({ ...targets, [item.id]: value })}
                        options={(tasks.data ?? []).map((task) => ({ value: String(task.id), label: `#${task.id} ${task.title}` }))}
                      />
                    </Field>
                  )}
                </motion.div>
              );
            })}
          </AnimatePresence>
        </div>
      )}

      {editing && (
        <TaskForm
          task={{ ...editing, id: undefined }}
          onClose={() => setEditing(null)}
          onSubmit={(values) => accept(editing, values)}
          pending={decision.isPending}
          error={decision.error}
        />
      )}
    </div>
  );
}
