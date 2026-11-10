import { useEffect, useState } from 'react';
import { Alert, Button, Collapse, Group, Loader, Paper, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { request } from './api';

export default function LuchebaBrowser({ active = false }) {
  const [opened, setOpened] = useState(false);
  const [streamFailed, setStreamFailed] = useState(false);
  useEffect(() => { if (active) setOpened(true); }, [active]);
  useEffect(() => { if (opened) setStreamFailed(false); }, [opened]);
  const screen = useQuery({
    queryKey: ['luna-browser-screen'],
    queryFn: () => request('/integrations/context/browser/screen'),
    enabled: opened,
    refetchInterval: 2000,
    retry: false,
  });
  return <Paper withBorder p="sm"><Stack gap="xs">
    <Group>
      <Button variant="light" size="xs" onClick={() => setOpened(!opened)}>{opened ? 'Скрыть экран браузера Lucheba' : 'Экран браузера Lucheba'}</Button>
      {opened && screen.isFetching && <Loader size="xs" />}
    </Group>
    <Collapse in={opened}><Stack gap="xs">
      <Text size="xs" c="dimmed">Прямой поток окна Lucheba: личный кабинет или поиск. Агент тоже получает снимки после действий и может рассматривать страницу целиком.</Text>
      {screen.data ? <>
        <Text size="xs" style={{ overflowWrap: 'anywhere' }}>{screen.data.url}</Text>
        <Text size="xs" c="dimmed">Снимок: {new Date(screen.data.at).toLocaleTimeString()}</Text>
        {opened && <img src={streamFailed ? `data:${screen.data.mime};base64,${screen.data.image}` : '/api/integrations/context/browser/stream'} onError={() => setStreamFailed(true)} alt="Экран браузера Lucheba" style={{ display: 'block', width: '100%', maxWidth: 1100, borderRadius: 8 }} />}
        {streamFailed && <Text size="xs" c="yellow">Поток прерван, показываем обновляемые снимки. Закрой и открой панель для переподключения.</Text>}
      </> : <Text size="sm" c="dimmed">Снимков пока нет. Они появятся, когда Lucheba откроет личный кабинет или поиск.</Text>}
      {screen.error && <Alert color="yellow">Не удалось обновить экран: {screen.error.message}</Alert>}
    </Stack></Collapse>
  </Stack></Paper>;
}
