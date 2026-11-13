import { Badge, Group, Stack, Text, Tooltip } from '@mantine/core';

export function DeadlineRisk({ reason }) {
  return reason ? <Tooltip multiline w={260} label={`Большой риск не успеть: ${reason}`} events={{ hover: true, focus: true, touch: true }}>
    <Text component="span" tabIndex={0} c="orange" ml={6} style={{ display: 'inline-flex', verticalAlign: 'middle', cursor: 'help' }} aria-label={`Большой риск не успеть: ${reason}`}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M10.3 3.9 2 18.2A2 2 0 0 0 3.7 21h16.6a2 2 0 0 0 1.7-2.8L13.7 3.9a2 2 0 0 0-3.4 0Z" /><path d="M12 9v4m0 4h.01" /></svg>
    </Text>
  </Tooltip> : null;
}

export default function RankingAssessment({ assessment, compact = false }) {
  if (!assessment) return null;
  const plan = assessment.plan;
  const time = plan ? `${Math.round(plan.min_minutes / 60 * 10) / 10}–${Math.round(plan.max_minutes / 60 * 10) / 10} ч` : 'Время';
  return <Stack gap={5} maw={320}>
    {!compact && <Group gap={4}>
      {assessment.time_estimate && <Tooltip multiline w={260} label={assessment.time_estimate}><Badge size="xs" variant="light" color="purple">{time}</Badge></Tooltip>}
      {assessment.effort && <Tooltip multiline w={260} label={assessment.effort}><Badge size="xs" variant="light" color="gray">Силы</Badge></Tooltip>}
      {assessment.impact && <Tooltip multiline w={260} label={assessment.impact}><Badge size="xs" variant="light" color="gray">Эффект</Badge></Tooltip>}
    </Group>}
    <Tooltip multiline w={280} label={assessment.reason} events={{ hover: true, focus: true, touch: true }}>
      <Text component="button" type="button" size="xs" c="dimmed" style={{ cursor: 'help', background: 'none', border: 0, padding: 0, textAlign: 'left', width: 'fit-content' }}>Почему такой порядок</Text>
    </Tooltip>
    {!compact && plan && <details>
      <summary style={{ cursor: 'pointer', fontSize: 11, color: 'var(--mantine-color-dimmed)' }}>Этапы работы</summary>
      <Stack gap={5} mt={6}>
        {assessment.time_estimate && <Text size="xs" c="dimmed">Время: {assessment.time_estimate}</Text>}
        {assessment.effort && <Text size="xs" c="dimmed">Силы: {assessment.effort}</Text>}
        {assessment.impact && <Text size="xs" c="dimmed">Эффект: {assessment.impact}</Text>}
        {plan?.steps.map((step, index) => <Text key={index} size="xs">{index + 1}. {step.title} · {step.min_minutes}–{step.max_minutes} мин{step.wait_minutes > 0 ? `; ожидание ${step.wait_minutes} мин` : ''}</Text>)}
        {plan?.assumptions.map((text, index) => <Text key={index} size="xs" c="dimmed">{text}</Text>)}
      </Stack>
    </details>}
  </Stack>;
}
