import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, Expand, Globe } from 'lucide-react';
import { request } from './api';
import useBrowserScreen from './useBrowserScreen';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/dialog';
import { Field, Input, PasswordField } from '@/components/ui/input';
import { SelectField } from '@/components/ui/select';
import { Notice } from '@/components/ui/surface';
import { CollapseSection } from '@/lib/motion';

export default function ContextBrowser({ taskId, disabled, initialSiteId = null }) {
  const client = useQueryClient();
  const [opened, setOpened] = useState(Boolean(initialSiteId));
  const [profileId, setProfileId] = useState(initialSiteId);
  const [formOpened, setFormOpened] = useState(false);
  const [credentials, setCredentials] = useState({ name: '', url: '', username: '', password: '' });
  const [typing, setTyping] = useState('');
  const [screen, setScreen] = useState(null);
  const [expanded, setExpanded] = useState(false);

  const live = useBrowserScreen(opened && Boolean(screen));
  const path = `/integrations/tasks/${taskId}/monocle/browser`;
  const sitesPath = '/integrations/monocle/browser/sites';
  const sites = useQuery({ queryKey: ['context-browser-sites'], queryFn: () => request(sitesPath), enabled: opened });
  const selected = useQuery({ queryKey: ['context-browser', taskId], queryFn: () => request(path), enabled: opened && Boolean(taskId) });

  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ['context-browser-sites'] });
    if (taskId) {
      await client.invalidateQueries({ queryKey: ['context-browser', taskId] });
      await client.invalidateQueries({ queryKey: ['context', taskId] });
      await client.invalidateQueries({ queryKey: ['tasks'] });
    }
  };

  const select = useMutation({
    mutationFn: (site_id) => (taskId ? request(path, { method: 'PUT', body: JSON.stringify({ site_id }) }) : Promise.resolve({ site_id })),
    onSuccess: (data) => {
      setProfileId(data.site_id);
      setScreen(null);
      return refresh();
    },
  });

  const save = useMutation({
    mutationFn: () => request(sitesPath, { method: 'POST', body: JSON.stringify(credentials) }),
    onSuccess: async (site) => {
      setCredentials({ name: '', url: '', username: '', password: '' });
      setFormOpened(false);
      select.mutate(site.id);
      await refresh();
    },
  });

  const forget = useMutation({
    mutationFn: () => request(`${sitesPath}/${siteId}`, { method: 'DELETE' }),
    onSuccess: () => {
      client.setQueryData(['luna-browser-screen'], null);
      select.mutate(null);
    },
  });

  const action = useMutation({
    mutationFn: (values) => request(taskId ? `${path}/action` : '/integrations/monocle/browser/manual', {
      method: 'POST',
      body: JSON.stringify({ site_id: siteId, ...values }),
    }),
    onSuccess: (data) => {
      setScreen(data);
      return refresh();
    },
  });

  const busy = disabled || select.isPending || save.isPending || forget.isPending || action.isPending;
  const siteId = taskId ? selected.data?.site_id : profileId;
  const available = sites.data?.some((site) => site.id === siteId);
  const error = sites.error || selected.error || select.error || save.error || forget.error || action.error;
  const field = (name) => (event) => setCredentials({ ...credentials, [name]: event.currentTarget.value });

  function screenImage(large = false) {
    return (
      <img
        width={live.frame?.width ?? screen?.width ?? 1100}
        height={live.frame?.height ?? screen?.height ?? 750}
        src={live.frame ? `data:${live.frame.mime};base64,${live.frame.image}` : `data:${screen?.mime ?? 'image/png'};base64,${screen?.image}`}
        alt="Браузер личного кабинета — нажми на нужный элемент"
        className="block w-full h-auto border border-[#303030]"
        style={{ maxWidth: large ? 1100 : 420, cursor: busy ? 'wait' : 'crosshair' }}
        onClick={(event) => {
          if (busy) return;
          const rect = event.currentTarget.getBoundingClientRect();
          action.mutate({
            action: 'click',
            x: Math.min(1099, Math.max(0, Math.floor(((event.clientX - rect.left) * (screen?.width ?? live.frame?.width ?? 1100)) / rect.width))),
            y: Math.min(749, Math.max(0, Math.floor(((event.clientY - rect.top) * (screen?.height ?? live.frame?.height ?? 750)) / rect.height))),
          });
        }}
      />
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {!initialSiteId && (
        <Button
          variant="default"
          size="sm"
          className="w-fit"
          leftIcon={opened ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          onClick={() => setOpened(!opened)}
        >
          <Globe className="h-3.5 w-3.5 text-[#929292]" />
          {opened ? 'Скрыть браузер' : 'Браузер ЛК'}
        </Button>
      )}

      <CollapseSection open={opened}>
        <div className="flex flex-col gap-2.5 border border-[#262626] bg-[#0D0D0D] p-3">
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Личный кабинет" className="flex-1 min-w-[220px]">
              <SelectField
                value={available ? siteId : null}
                clearable
                clearLabel="Автовыбор"
                placeholder="Автовыбор"
                options={(sites.data ?? []).map((site) => ({ value: site.id, label: site.name }))}
                disabled={busy || sites.isPending}
                onChange={(id) => select.mutate(id)}
              />
            </Field>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => setFormOpened(!formOpened)}>
              {formOpened ? 'Скрыть' : '+ Кабинет'}
            </Button>
          </div>

          <CollapseSection open={formOpened}>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 border border-[#2A2A2A] bg-[#111111] p-2.5">
              <Field label="Название">
                <Input placeholder="ЛК университета" value={credentials.name} onChange={field('name')} maxLength={100} />
              </Field>
              <Field label="Адрес входа">
                <Input placeholder="https://portal.example.edu" value={credentials.url} onChange={field('url')} autoComplete="off" />
              </Field>
              <Field label="Логин">
                <Input value={credentials.username} onChange={field('username')} autoComplete="off" />
              </Field>
              <Field label="Пароль">
                <PasswordField value={credentials.password} onChange={field('password')} autoComplete="new-password" />
              </Field>
              <div className="sm:col-span-2">
                <Button
                  size="sm"
                  variant="primary"
                  disabled={busy || !credentials.name || !credentials.url || !credentials.username || !credentials.password}
                  onClick={() => save.mutate()}
                >
                  Сохранить кабинет
                </Button>
              </div>
            </div>
          </CollapseSection>

          {available && (
            <div className="flex flex-col gap-2">
              <div className="flex flex-wrap gap-1.5">
                <Button size="sm" variant="primary" disabled={busy} onClick={() => action.mutate({ action: 'login' })}>
                  Войти
                </Button>
                <Button size="sm" variant="default" disabled={busy} onClick={() => action.mutate({ action: 'open' })}>
                  Страница входа
                </Button>
                <Button size="sm" variant="default" disabled={busy} onClick={() => action.mutate({ action: 'state' })}>
                  Снимок
                </Button>
                <Button
                  size="sm"
                  variant="danger-ghost"
                  disabled={busy}
                  onClick={() => {
                    if (window.confirm('Удалить сохранённый вход? Все браузерные сессии твоего аккаунта будут очищены.')) {
                      forget.mutate();
                    }
                  }}
                >
                  Забыть
                </Button>
              </div>

              {live.error && <Notice tone="warning">Переподключаем экран браузера…</Notice>}
              {screen?.notice && <Notice tone="info">{screen.notice.reason}</Notice>}

              {(screen?.image || live.frame?.image) && (
                <div className="flex flex-col gap-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[11px] text-[#929292] truncate">{screen?.url ?? live.frame?.url}</span>
                    <Button size="xs" variant="subtle" leftIcon={<Expand className="h-3 w-3" />} onClick={() => setExpanded(true)}>
                      Увеличить
                    </Button>
                  </div>
                  {screenImage(false)}
                  <div className="text-[11px] text-[#7D7D7D]">Клик по снимку — нажатие; поле ниже — ввод 2FA/кода.</div>
                  <Modal opened={expanded} onClose={() => setExpanded(false)} title="Личный кабинет" size="xl">
                    {screenImage(true)}
                  </Modal>
                  <div className="flex flex-wrap items-end gap-2">
                    <Field label="Ввод в поле" className="flex-1 min-w-[200px]">
                      <PasswordField value={typing} onChange={(event) => setTyping(event.currentTarget.value)} autoComplete="off" />
                    </Field>
                    <Button
                      size="sm"
                      variant="primary"
                      disabled={busy || !typing}
                      onClick={() => {
                        const value = typing;
                        setTyping('');
                        action.mutate({ action: 'type', text: value });
                      }}
                    >
                      Ввести
                    </Button>
                    {['Enter', 'Tab', 'Control+A', 'Backspace'].map((key) => (
                      <Button key={key} size="sm" variant="default" disabled={busy} onClick={() => action.mutate({ action: 'press', text: key })}>
                        {key}
                      </Button>
                    ))}
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => action.mutate({ action: 'scroll', delta: -500 })}>Выше</Button>
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => action.mutate({ action: 'scroll', delta: 500 })}>Ниже</Button>
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => action.mutate({ action: 'back' })}>Назад</Button>
                  </div>
                </div>
              )}
            </div>
          )}

          {error && <Notice tone="error">{error.message}</Notice>}
        </div>
      </CollapseSection>
    </div>
  );
}
