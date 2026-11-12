import { useState } from 'react';
import { Alert, Button, Group, Paper, Stack, Text, Textarea } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { request } from './api';

const services = { '\u0410\u0440\u0440\u043e\u0434\u0435\u0441': 'arrodes', Arrodes: 'arrodes', Codex: 'arrodes', Monocle: 'monocle', Lucheba: 'monocle', Straus: 'straus', Ameba: 'ameba' };

export default function AgentQuestion({ question, agent }) {
  const [text, setText] = useState('');
  const answer = useMutation({
    mutationFn: () => request(`/integrations/agents/${services[agent]}/questions/${question.id}/answer`, {
      method: 'POST', body: JSON.stringify({ text: text.trim() }),
    }),
  });
  return <Paper withBorder p="sm" mt="xs" role="region" aria-label="Вопрос агента">
    <form onSubmit={(event) => { event.preventDefault(); if (text.trim() && !answer.isPending && !answer.isSuccess) answer.mutate(); }}>
      <Stack gap="xs">
        <Text size="sm" fw={600} style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{question.question}</Text>
        {question.options?.length > 0 && <Group gap="xs">{question.options.map((option, index) => <Button key={index} size="xs"
          variant={text === option ? 'light' : 'default'} disabled={answer.isPending || answer.isSuccess}
          styles={{ root: { height: 'auto', minHeight: 36 }, label: { whiteSpace: 'normal', paddingBlock: 6 } }} onClick={() => setText(option)}>{option}</Button>)}</Group>}
        <Textarea label="Ваш ответ" value={text} onChange={(event) => setText(event.currentTarget.value)} autosize minRows={2} maxRows={8} maxLength={12000} disabled={answer.isPending || answer.isSuccess} />
        <Group><Button type="submit" loading={answer.isPending} disabled={!text.trim() || answer.isSuccess}>{answer.isSuccess ? 'Ответ отправлен' : 'Ответить и продолжить'}</Button></Group>
        {answer.error && <Alert color="red">{answer.error.message}</Alert>}
      </Stack>
    </form>
  </Paper>;
}
