import { Badge, Group, Stack, Text, Tooltip } from '@mantine/core';

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
