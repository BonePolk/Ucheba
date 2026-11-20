import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Globe, Pencil, Plus, Search, Trash2 } from 'lucide-react';
import { request } from './api';
import ContextBrowser from './ContextBrowser';
import { Button, IconButton } from '@/components/ui/button';
import { Modal } from '@/components/ui/dialog';
import { Field, Input, PasswordField } from '@/components/ui/input';
import { Badge, EmptyState, Notice, PageLoader, SectionHeader } from '@/components/ui/surface';
import { AnimatePresence, LAYOUT_TRANSITION, motion, useReducedMotion } from '@/lib/motion';

const empty = { name: '', url: '', username: '', password: '', description: '', group: '', color: '#7f49b4' };

export default function Cabinets() {
  const client = useQueryClient();
  const reduced = useReducedMotion();
  const [search, setSearch] = useState('');
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(empty);
  const [viewing, setViewing] = useState(null);

  const sites = useQuery({
    queryKey: ['context-browser-sites'],
    queryFn: () => request('/integrations/monocle/browser/sites'),
  });
  const queue = useQuery({
    queryKey: ['agent-queue'],
    queryFn: () => request('/integrations/queue'),
    refetchInterval: 2000,
  });

  const save = useMutation({
    mutationFn: () => request(`/integrations/monocle/browser/sites${editing.id ? `/${editing.id}` : ''}`, {
      method: editing.id ? 'PATCH' : 'POST',
      body: JSON.stringify(form),
    }),
    onSuccess: async () => {
      setEditing(null);
      setForm(empty);
      await client.invalidateQueries({ queryKey: ['context-browser-sites'] });
    },
  });

  const remove = useMutation({
    mutationFn: (id) => request(`/integrations/monocle/browser/sites/${id}`, { method: 'DELETE' }),
    onSuccess: async () => {
      client.setQueryData(['luna-browser-screen'], null);
      await client.invalidateQueries({ queryKey: ['context-browser-sites'] });
    },
  });

  const field = (name) => (event) => setForm({ ...form, [name]: event.currentTarget.value });
  const visible = (sites.data ?? []).filter((site) => `${site.name} ${site.group} ${site.url}`.toLowerCase().includes(search.toLowerCase()));

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <SectionHeader title="Сохранённые входы" meta={sites.data ? `${sites.data.length}` : undefined} />
        <div className="flex flex-wrap sm:flex-nowrap items-center gap-2 w-full sm:w-auto">
          <Input
            aria-label="Поиск кабинетов"
            placeholder="Найти кабинет, группу или адрес…"
            value={search}
            onChange={(event) => setSearch(event.currentTarget.value)}
            leftIcon={<Search className="h-3.5 w-3.5" />}
            className="flex-1 min-w-[140px] sm:w-[220px] sm:flex-none"
          />
          <Button variant="primary" leftIcon={<Plus className="h-3.5 w-3.5" />} onClick={() => { setForm(empty); setEditing({}); }}>
            Добавить вход
          </Button>
        </div>
      </div>

      {sites.error && <Notice tone="error">{sites.error.message}</Notice>}
      {remove.error && <Notice tone="error">{remove.error.message}</Notice>}

      {sites.isPending ? (
        <PageLoader />
      ) : visible.length === 0 ? (
        <EmptyState icon={<Globe className="h-5 w-5" />} title="Кабинетов нет" />
      ) : (
        <div className="border border-[#303030] bg-[#111111] divide-y divide-[#1E1E1E]">
          <AnimatePresence initial={false}>
            {visible.map((site) => (
              <motion.div
                key={site.id}
                layout={reduced ? false : 'position'}
                initial={reduced ? false : { opacity: 0, y: 3 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={reduced ? { duration: 0.01 } : LAYOUT_TRANSITION}
                className="flex flex-wrap items-center justify-between gap-3 px-3 py-2 border-l-2 hover:bg-[#161616] transition-colors"
                style={{ borderLeftColor: site.color || '#7f49b4' }}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[13px] font-medium text-[#EAEAEA]">{site.name}</span>
                    {site.group && <Badge variant="outline">{site.group}</Badge>}
                    {site.has_credentials && <Badge variant="success">Сохранён</Badge>}
                  </div>
                  <div className="text-[11px] text-[#929292] truncate">{site.url}</div>
                  {site.description && <div className="text-[12px] text-[#B8B8B8] truncate mt-0.5">{site.description}</div>}
                </div>

                <div className="flex items-center gap-1 shrink-0">
                  <Button size="sm" variant="default" onClick={() => setViewing(site)}>Браузер</Button>
                  <IconButton
                    size="icon-sm"
                    variant="ghost"
                    aria-label={`Настроить ${site.name}`}
                    onClick={() => {
                      setForm({ ...empty, ...site, username: '', password: '' });
                      setEditing(site);
                    }}
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </IconButton>
                  <IconButton
                    size="icon-sm"
                    variant="danger-ghost"
                    aria-label={`Забыть вход ${site.name}`}
                    disabled={remove.isPending}
                    onClick={() => {
                      if (window.confirm(`Удалить сохранённый вход «${site.name}»? Все браузерные сессии аккаунта будут очищены.`)) {
                        remove.mutate(site.id);
                      }
                    }}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </IconButton>
                </div>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}

      <Modal opened={Boolean(editing)} onClose={() => setEditing(null)} title={editing?.id ? 'Настройки кабинета' : 'Новый кабинет'} size="md">
        <div className="flex flex-col gap-2.5">
          <Field label="Название">
            <Input value={form.name} onChange={field('name')} maxLength={100} />
          </Field>
          <Field label="Страница входа">
            <Input value={form.url} onChange={field('url')} />
          </Field>
          <div className="grid grid-cols-1 sm:grid-cols-[1fr_110px] gap-2">
            <Field label="Группа">
              <Input value={form.group} onChange={field('group')} maxLength={80} />
            </Field>
            <Field label="Цвет метки">
              <div className="flex items-center gap-1.5 h-[30px] max-md:min-h-[40px] bg-[#111111] border border-[#303030] px-2">
                <input
                  type="color"
                  aria-label="Цвет"
                  value={form.color || '#7f49b4'}
                  onChange={(event) => setForm({ ...form, color: event.currentTarget.value })}
                  className="h-5 w-6 bg-transparent border-0 p-0 cursor-pointer"
                />
                <span className="text-[11px] font-mono text-[#929292]">{form.color}</span>
              </div>
            </Field>
          </div>
          <Field label="Описание">
            <Input value={form.description} onChange={field('description')} maxLength={300} />
          </Field>
          <Field label="Логин">
            <Input
              placeholder={editing?.id ? 'Оставить прежний' : ''}
              value={form.username}
              onChange={field('username')}
              autoComplete="off"
            />
          </Field>
          <Field label="Пароль">
            <PasswordField
              placeholder={editing?.has_credentials ? 'Сохранён; пустое поле сохраняет его' : ''}
              value={form.password}
              onChange={field('password')}
              autoComplete="new-password"
            />
          </Field>
          {save.error && <Notice tone="error">{save.error.message}</Notice>}
          <div className="flex justify-end gap-2 pt-2 border-t border-[#222222]">
            <Button variant="default" onClick={() => setEditing(null)}>Отмена</Button>
            <Button
              variant="primary"
              loading={save.isPending}
              disabled={!form.name || !form.url || (!editing?.id && (!form.username || !form.password))}
              onClick={() => save.mutate()}
            >
              Сохранить
            </Button>
          </div>
        </div>
      </Modal>

      <Modal opened={Boolean(viewing)} onClose={() => setViewing(null)} title={viewing?.name} size="xl">
        {viewing && (
          <div className="flex flex-col gap-2">
            {Boolean(queue.data?.agents_busy ?? queue.data?.busy) && (
              <Notice tone="warning">Агенты используют браузер; ручное управление доступно после их завершения.</Notice>
            )}
            <ContextBrowser key={viewing.id} initialSiteId={viewing.id} disabled={Boolean(queue.data?.agents_busy ?? queue.data?.busy)} />
          </div>
        )}
      </Modal>
    </div>
  );
}
