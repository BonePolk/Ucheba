import { useEffect, useState } from 'react';
import { Alert, Button, Collapse, Group, Modal, Paper, Stack, Text } from '@mantine/core';
import useBrowserScreen from './useBrowserScreen';

export default function MonocleBrowser({ active = false }) {
  const [opened, setOpened] = useState(() => new URLSearchParams(window.location.search).get('browser') === '1');
  const [expanded, setExpanded] = useState(false);
  useEffect(() => { if (active) setOpened(true); }, [active]);
  const { frame, error } = useBrowserScreen(opened || expanded);
  const image = frame ? `data:${frame.mime};base64,${frame.image}` : null;
  return <Paper withBorder p="xs" radius="md" maw={360}><Stack gap={6}>
    <Group gap="xs" justify="space-between"><Button variant="subtle" size="compact-xs" onClick={() => setOpened(!opened)}>{opened ? 'Скрыть браузер' : 'Браузер агента'}</Button>{image && <Button variant="subtle" size="compact-xs" onClick={() => setExpanded(true)}>Увеличить ↗</Button>}</Group>
    <Collapse in={opened}>
      {image ? <button type="button" onClick={() => setExpanded(true)} style={{ display: 'block', width: '100%', border: 0, padding: 0, background: 'none', cursor: 'zoom-in' }}><img src={image} alt="Увеличить экран браузера агента" style={{ display: 'block', width: '100%', borderRadius: 6 }} /></button> : <Text size="xs" c="dimmed">Браузер ещё не открыт.</Text>}
      {error && <Text size="xs" c="yellow">Переподключаемся…</Text>}
    </Collapse>
    <Modal opened={expanded} onClose={() => setExpanded(false)} title="Браузер агента" size="xl" centered>
      {image && <><Text size="xs" c="dimmed" style={{ overflowWrap: 'anywhere' }}>{frame.url}</Text><img src={image} alt="Экран браузера агента" style={{ display: 'block', width: '100%', borderRadius: 8, marginTop: 8 }} /></>}
      {error && <Alert color="yellow">Поток прерван. Переподключаемся…</Alert>}
    </Modal>
  </Stack></Paper>;
}
