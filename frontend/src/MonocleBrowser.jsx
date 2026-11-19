import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Expand } from 'lucide-react';
import { request } from './api';
import useBrowserScreen from './useBrowserScreen';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/dialog';
import { Field, PasswordField } from '@/components/ui/input';
import { Notice } from '@/components/ui/surface';
import { CollapseSection } from '@/lib/motion';

export default function MonocleBrowser({ active = false }) {
  const [opened, setOpened] = useState(() => new URLSearchParams(window.location.search).get('browser') === '1');
  const [expanded, setExpanded] = useState(false);
  const [typing, setTyping] = useState('');
  const client = useQueryClient();

  const controls = useQuery({
    queryKey: ['browser-handoff'],
    queryFn: () => request('/integrations/monocle/browser/handoff', {
      method: 'POST',
      body: JSON.stringify({ action: 'status' }),
    }),
    enabled: opened || expanded,
    refetchInterval: 1000,
  });

  const action = useMutation({
    mutationFn: (values) => request('/integrations/monocle/browser/handoff', {
      method: 'POST',
      body: JSON.stringify(values),
    }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['browser-handoff'] }),
  });

  useEffect(() => {
    if (active) setOpened(true);
  }, [active]);

  const { frame, error } = useBrowserScreen(opened || expanded);
  const paused = controls.data?.paused;
  const busy = action.isPending;
  const image = frame ? `data:${frame.mime};base64,${frame.image}` : null;

  function click(event) {
    if (!paused || busy) return;
    const rect = event.currentTarget.getBoundingClientRect();
    action.mutate({
      action: 'click',
      x: Math.min(1099, Math.max(0, Math.floor(((event.clientX - rect.left) * (frame?.width ?? 1100)) / rect.width))),
      y: Math.min(749, Math.max(0, Math.floor(((event.clientY - rect.top) * (frame?.height ?? 750)) / rect.height))),
    });
  }

  return (
    <div className="border border-[#303030] bg-[#0D0D0D] p-2 flex flex-col gap-2 w-full">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button size="xs" variant="subtle" onClick={() => setOpened(!opened)}>
          {opened ? 'Скрыть браузер' : 'Браузер агента'}
        </Button>
        <Button size="xs" variant={paused ? 'primary' : 'subtle'} leftIcon={<Expand className="h-3 w-3" />} onClick={() => setExpanded(true)}>
          {paused ? 'Пройти капчу / продолжить' : 'Увеличить'}
        </Button>
      </div>

      <CollapseSection open={opened}>
        <div className="flex flex-col gap-1">
          <div className="bg-[#090909] border border-[#222222]" style={{ aspectRatio: `${frame?.width ?? 1100} / ${frame?.height ?? 750}` }}>
            {image ? (
              <button
                type="button"
                onClick={() => setExpanded(true)}
                className="block w-full border-0 p-0 bg-transparent cursor-zoom-in"
              >
                <img
                  width={frame?.width ?? 1100}
                  height={frame?.height ?? 750}
                  src={image}
                  alt="Увеличить экран браузера агента"
                  className="block w-full h-auto"
                />
              </button>
            ) : (
              <div className="h-full min-h-[120px] flex items-center justify-center text-[11px] text-[#787878]">
                Браузер ещё не открыт
              </div>
            )}
          </div>
          {error && <div className="text-[11px] text-[#D4A24C]">Переподключаемся…</div>}
        </div>
      </CollapseSection>

      <Modal opened={expanded} onClose={() => setExpanded(false)} title="Браузер агента" size="xl">
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant={paused ? 'primary' : 'default'}
              loading={busy}
              onClick={() => action.mutate({ action: paused ? 'resume' : 'pause' })}
            >
              {paused ? 'Готово — продолжить работу агента' : 'Взять управление / пройти капчу'}
            </Button>
            {frame?.url && <span className="text-[11px] text-[#929292] break-all">{frame.url}</span>}
          </div>

          {paused && (
            <Notice tone="warning">
              Агент ждёт. Нажимайте на элементы снимка, чтобы пройти капчу или завершить вход. Затем верните управление кнопкой выше.
            </Notice>
          )}

          {image && (
            <img
              width={frame?.width ?? 1100}
              height={frame?.height ?? 750}
              src={image}
              alt="Экран браузера агента"
              onClick={click}
              className="block w-full h-auto border border-[#303030]"
              style={{ cursor: paused ? 'crosshair' : 'default' }}
            />
          )}

          {paused && (
            <div className="flex flex-col gap-2 border-t border-[#222222] pt-3">
              <div className="flex flex-wrap items-end gap-2">
                <Field label="Ввести в выбранное поле" className="w-full min-w-0 sm:flex-1 sm:min-w-[200px]">
                  <PasswordField
                    value={typing}
                    onChange={(event) => setTyping(event.currentTarget.value)}
                    autoComplete="off"
                  />
                </Field>
                <Button
                  variant="primary"
                  disabled={busy || !typing}
                  onClick={() => {
                    action.mutate({ action: 'type', text: typing });
                    setTyping('');
                  }}
                >
                  Ввести
                </Button>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {['Enter', 'Tab', 'Backspace', 'Control+A', 'Escape'].map((key) => (
                  <Button key={key} size="sm" variant="default" disabled={busy} onClick={() => action.mutate({ action: 'press', text: key })}>
                    {key}
                  </Button>
                ))}
                <Button size="sm" variant="outline" disabled={busy} onClick={() => action.mutate({ action: 'scroll', delta: -500 })}>Выше</Button>
                <Button size="sm" variant="outline" disabled={busy} onClick={() => action.mutate({ action: 'scroll', delta: 500 })}>Ниже</Button>
              </div>
            </div>
          )}

          {(action.error || controls.error) && (
            <Notice tone="error">{(action.error || controls.error).message}</Notice>
          )}
          {error && <Notice tone="warning">Поток прерван. Переподключаемся…</Notice>}
        </div>
      </Modal>
    </div>
  );
}
