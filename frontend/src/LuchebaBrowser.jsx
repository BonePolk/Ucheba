import { useEffect, useState } from 'react';
import { Alert, Button, Collapse, Group, Modal, Paper, PasswordInput, Stack, Text } from '@mantine/core';
import useBrowserScreen from './useBrowserScreen';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';

export default function MonocleBrowser({ active = false }) {
  const [opened, setOpened] = useState(() => new URLSearchParams(window.location.search).get('browser') === '1');
  const [expanded, setExpanded] = useState(false);
  const [typing, setTyping] = useState('');
  const client = useQueryClient();
  const controls = useQuery({
    queryKey: ['browser-handoff'],
    queryFn: () => request('/integrations/context/browser/handoff', { method: 'POST', body: JSON.stringify({ action: 'status' }) }),
    enabled: opened || expanded,
    refetchInterval: 1000,
  });
  const action = useMutation({
    mutationFn: (values) => request('/integrations/context/browser/handoff', { method: 'POST', body: JSON.stringify(values) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['browser-handoff'] }),
  });
  const paused = controls.data?.paused;
  const busy = action.isPending;
  function click(event) {
    if (!paused || busy) return;
    const rect = event.currentTarget.getBoundingClientRect();
    action.mutate({ action: 'click',
      x: Math.min(1099, Math.max(0, Math.floor((event.clientX - rect.left) * (frame?.width ?? 1100) / rect.width))),
      y: Math.min(749, Math.max(0, Math.floor((event.clientY - rect.top) * (frame?.height ?? 750) / rect.height))),
    });
  }
  useEffect(() => { if (active) setOpened(true); }, [active]);
  const { frame, error } = useBrowserScreen(opened || expanded);
  const image = frame ? `data:${frame.mime};base64,${frame.image}` : null;
  if (!image) return null;
  return <Paper withBorder p="xs" radius="md" maw={280} className="agent-browser"><Stack gap={6}>
    <Group gap="xs" justify="space-between"><Button variant="subtle" size="compact-xs" onClick={() => setOpened(!opened)}>{opened ? 'Скрыть браузер' : 'Браузер агента'}</Button><Button variant="subtle" size="compact-xs" onClick={() => setExpanded(true)}>{paused ? 'Пройти капчу / продолжить' : 'Увеличить ↗'}</Button></Group>
    <Collapse in={opened}>
      <div style={{ aspectRatio: `${frame?.width ?? 1100} / ${frame?.height ?? 750}` }}>{image ? <button type="button" onClick={() => setExpanded(true)} style={{ display: 'block', width: '100%', border: 0, padding: 0, background: 'none', cursor: 'zoom-in' }}><img width={frame?.width ?? 1100} height={frame?.height ?? 750} src={image} alt="Увеличить экран браузера агента" style={{ display: 'block', width: '100%', height: 'auto', borderRadius: 0 }} /></button> : <Text size="xs" c="dimmed">Браузер ещё не открыт.</Text>}</div>
      {error && <Text size="xs" c="yellow">Переподключаемся…</Text>}
    </Collapse>
    <Modal opened={expanded} onClose={() => setExpanded(false)} title="Браузер агента" size="xl" centered>
      <Group mb="xs">
        <Button loading={busy} onClick={() => action.mutate({ action: paused ? 'resume' : 'pause' })}>{paused ? 'Готово — продолжить работу агента' : 'Взять управление / пройти капчу'}</Button>
      </Group>
      {paused && <Text size="xs" mb="xs">Агент ждёт. Нажимайте на элементы снимка, чтобы пройти капчу или завершить вход. Затем верните управление кнопкой выше.</Text>}
      {image && <><Text size="xs" c="dimmed" style={{ overflowWrap: 'anywhere' }}>{frame.url}</Text><img width={frame?.width ?? 1100} height={frame?.height ?? 750} src={image} alt="Экран браузера агента" onClick={click} style={{ display: 'block', width: '100%', height: 'auto', borderRadius: 0, marginTop: 8, cursor: paused ? 'crosshair' : 'default' }} /></>}
      {paused && <Stack gap="xs" mt="sm">
        <Group align="end"><PasswordInput label="Ввести в выбранное поле" value={typing} onChange={(event) => setTyping(event.currentTarget.value)} autoComplete="off" /><Button disabled={busy || !typing} onClick={() => { action.mutate({ action: 'type', text: typing }); setTyping(''); }}>Ввести</Button></Group>
        <Group>{['Enter', 'Tab', 'Backspace', 'Control+A', 'Escape'].map((key) => <Button key={key} size="compact-xs" disabled={busy} variant="default" onClick={() => action.mutate({ action: 'press', text: key })}>{key}</Button>)}</Group>
        <Group><Button size="compact-xs" disabled={busy} onClick={() => action.mutate({ action: 'scroll', delta: -500 })}>Выше</Button><Button size="compact-xs" disabled={busy} onClick={() => action.mutate({ action: 'scroll', delta: 500 })}>Ниже</Button></Group>
      </Stack>}
      {(action.error || controls.error) && <Alert color="red">{(action.error || controls.error).message}</Alert>}
      {error && <Alert color="yellow">Поток прерван. Переподключаемся…</Alert>}
    </Modal>
  </Stack></Paper>;
}
