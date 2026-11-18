import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Pencil, Plus, Search, Trash2 } from 'lucide-react';
import { request, agentName } from './api';
import { Button, IconButton } from '@/components/ui/button';
import { Field, Input, Textarea } from '@/components/ui/input';
import { Badge, EmptyState, Notice, PageLoader, SectionHeader } from '@/components/ui/surface';
import { AnimatePresence, CollapseSection, LAYOUT_TRANSITION, motion } from '@/lib/motion';

export default function AgentMemory() {
  const client = useQueryClient();
  const [editing, setEditing] = useState(null);
  const [adding, setAdding] = useState(false);
  const [newFact, setNewFact] = useState('');
  const [search, setSearch] = useState('');
  const [expandedKeys, setExpandedKeys] = useState({});

  const memory = useQuery({
    queryKey: ['agent-memory'],
    queryFn: () => request('/auth/agent-memory'),
    refetchInterval: 10000,
  });

  const invalidate = () => {
    client.invalidateQueries({ queryKey: ['agent-memory'] });
    client.invalidateQueries({ queryKey: ['ameba-ranking'] });
  };

  const save = useMutation({
    mutationFn: () => request('/auth/agent-memory', { method: 'PUT', body: JSON.stringify(editing) }),
    onSuccess: () => {
      setEditing(null);
      invalidate();
    },
  });

  const create = useMutation({
    mutationFn: (fact) => request('/auth/agent-memory', { method: 'POST', body: JSON.stringify(fact) }),
    onSuccess: () => {
      setNewFact('');
      setAdding(false);
      invalidate();
    },
  });

  const remove = useMutation({
    mutationFn: (key) => request(`/auth/agent-memory/${encodeURIComponent(key)}`, { method: 'DELETE' }),
    onSuccess: invalidate,
  });

  const items = (memory.data ?? []).filter((fact) => {
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return `${fact.text} ${agentName(fact.agent)}`.toLowerCase().includes(q);
  });

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <SectionHeader
          title="Память"
          meta={memory.data ? `${memory.data.length}` : undefined}
        />
        <div className="flex items-center gap-2">
          <Input
            aria-label="Поиск фактов"
            placeholder="Поиск…"
            value={search}
            onChange={(event) => setSearch(event.currentTarget.value)}
            leftIcon={<Search className="h-3.5 w-3.5" />}
            className="w-[200px]"
          />
          <Button size="default" variant="primary" leftIcon={<Plus className="h-3.5 w-3.5" />} onClick={() => setAdding(!adding)}>
            Факт
          </Button>
        </div>
      </div>

      <CollapseSection open={adding}>
        <div className="border border-[#303030] bg-[#111111] p-3 flex flex-col gap-2">
          <Field label="Факт или предпочтение">
            <Textarea
              rows={2}
              maxLength={1500}
              placeholder="Например: утром тяжело сосредоточиться"
              value={newFact}
              onChange={(event) => setNewFact(event.currentTarget.value)}
            />
          </Field>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="primary"
              loading={create.isPending}
              disabled={!newFact.trim()}
              onClick={() => create.mutate({ text: newFact.trim() })}
            >
              Сохранить
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setAdding(false)}>Отмена</Button>
          </div>
        </div>
      </CollapseSection>

      {(memory.error || save.error || remove.error || create.error) && (
        <Notice tone="error">{(memory.error || save.error || remove.error || create.error).message}</Notice>
      )}

      {memory.isPending ? (
        <PageLoader />
      ) : items.length === 0 ? (
        <EmptyState title={search ? 'Ничего не найдено' : 'Записей нет'} />
      ) : (
        <div className="border border-[#303030] bg-[#111111] divide-y divide-[#1E1E1E]">
          <AnimatePresence initial={false}>
            {items.map((fact) => {
              const isEditing = editing?.key === fact.key;
              const isExpanded = Boolean(expandedKeys[fact.key]);
              const isLong = fact.text.length > 90 || fact.text.includes('\n');

              return (
                <motion.div
                  key={fact.key}
                  layout="position"
                  initial={{ opacity: 0, y: 3 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  transition={LAYOUT_TRANSITION}
                  className="px-3 py-2"
                >
                  {isEditing ? (
                    <div className="flex flex-col gap-2">
                      <Textarea
                        rows={2}
                        maxLength={1500}
                        value={editing.text}
                        onChange={(event) => setEditing({ ...editing, text: event.currentTarget.value })}
                      />
                      <div className="flex items-center gap-1.5">
                        <Button size="sm" variant="primary" loading={save.isPending} disabled={!editing.text.trim()} onClick={() => save.mutate()}>
                          Сохранить
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>Отмена</Button>
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div
                          onClick={isLong ? () => setExpandedKeys((prev) => ({ ...prev, [fact.key]: !prev[fact.key] })) : undefined}
                          className={`text-[13px] text-[#EAEAEA] whitespace-pre-wrap break-words ${isLong ? 'cursor-pointer' : ''} ${!isExpanded ? 'line-clamp-1' : ''}`}
                        >
                          {fact.text}
                        </div>
                        <div className="flex flex-wrap items-center gap-2 mt-1 text-[11px] text-[#929292]">
                          <Badge variant="outline">{agentName(fact.agent)}</Badge>
                          <span className="tabular-nums">{new Date(fact.updated_at).toLocaleDateString()}</span>
                          {isLong && (
                            <button
                              type="button"
                              onClick={() => setExpandedKeys((prev) => ({ ...prev, [fact.key]: !prev[fact.key] }))}
                              className="text-[#CFA9F5] hover:underline cursor-pointer"
                            >
                              {isExpanded ? 'Свернуть' : 'Ещё'}
                            </button>
                          )}
                        </div>
                      </div>

                      <div className="flex items-center gap-1 shrink-0">
                        <IconButton
                          size="icon-sm"
                          variant="ghost"
                          aria-label="Изменить факт"
                          onClick={() => setEditing({ key: fact.key, text: fact.text })}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </IconButton>
                        <IconButton
                          size="icon-sm"
                          variant="danger-ghost"
                          aria-label="Удалить факт"
                          disabled={remove.isPending && remove.variables === fact.key}
                          onClick={() => remove.mutate(fact.key)}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </IconButton>
                      </div>
                    </div>
                  )}
                </motion.div>
              );
            })}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}
