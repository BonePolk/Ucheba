import { useState } from 'react';
import { Accordion, Alert, Badge, Button, Group, Loader, Paper, Select, Stack, Text, TextInput, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { agentRequest, request } from './api';
import AgentProgress from './AgentProgress';

const days = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];
const kinds = [{ value: 'work', label: 'Работа над задачами' }, { value: 'sleep', label: 'Сон' }, { value: 'meal', label: 'Еда' }, { value: 'break', label: 'Отдых' }, { value: 'busy', label: 'Учёба / занятость' }];
const patterns = [{ value: 'all', label: 'Каждую неделю' }, { value: 'even', label: 'Чётные недели' }, { value: 'odd', label: 'Нечётные недели' }];
const minute = (time) => Number(time.slice(0, 2)) * 60 + Number(time.slice(3));

export default function WeeklySchedule({ features = {} }) {
  const client = useQueryClient();
  const [draft, setDraft] = useState(null);
  const [link, setLink] = useState('');
  const [siteId, setSiteId] = useState(null);
  const [notes, setNotes] = useState('');
  const [importError, setImportError] = useState('');
  const query = useQuery({ queryKey: ['weekly-schedule'], queryFn: () => request('/auth/schedule') });
  const sites = useQuery({ queryKey: ['context-browser-sites'], queryFn: () => request('/integrations/context/browser/sites'), enabled: features.context !== false });
  const save = useMutation({ mutationFn: () => request('/auth/schedule', { method: 'PUT', body: JSON.stringify(draft ?? (query.data.configured ? query.data.schedule : { ...query.data.schedule, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC' })) }), onSuccess: async () => { setDraft(null); await client.invalidateQueries({ queryKey: ['weekly-schedule'] }); await client.invalidateQueries({ queryKey: ['jev-ranking'] }); } });
  const importing = useMutation({ mutationFn: () => agentRequest('/integrations/context/schedule/import', { method: 'POST', body: JSON.stringify({ site_id: siteId, url: link, notes }) }) });
  if (query.isPending) return <Loader size="sm" />;
  if (!query.data) return <Alert color="red">{query.error?.message}</Alert>;
  const week = draft ?? (query.data.configured ? query.data.schedule : { ...query.data.schedule, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC' });
  const update = (day, index, values) => setDraft({ ...week, days: week.days.map((slots, d) => d === day ? slots.map((slot, i) => i === index ? { ...slot, ...values } : slot) : slots) });
  function applyImport() {
    const next = { ...week, days: week.days.map((slots) => slots.map((slot) => ({ ...slot }))), events: [...(week.events ?? [])] };
    for (const lesson of importing.data.lessons) {
      if (minute(lesson.end) <= minute(lesson.start)) { setImportError('В импорте есть неверный интервал — уточни расписание у Straus'); return; }
      const slot = { kind: 'busy', title: lesson.title, start: lesson.start, end: lesson.end, week_pattern: lesson.week_pattern };
      if (lesson.date) {
        if (!next.events.some((event) => event.date === lesson.date && event.title === slot.title && event.start === slot.start && event.end === slot.end)) next.events.push({ ...slot, date: lesson.date });
        continue;
      }
      if (lesson.weekday == null || !next.days[lesson.weekday]) { setImportError('Straus не указал день недели или дату события'); return; }
      const slots = next.days[lesson.weekday];
      if (slots.some((item) => item.kind === 'busy' && item.title === slot.title && item.start === slot.start && item.end === slot.end && (item.week_pattern ?? 'all') === slot.week_pattern)) continue;
      const conflict = slots.some((item) => item.kind !== 'work' && item.start < slot.end && item.end > slot.start && ((item.week_pattern ?? 'all') === 'all' || slot.week_pattern === 'all' || item.week_pattern === slot.week_pattern));
      if (conflict) { setImportError(`Конфликт: ${lesson.title}, ${days[lesson.weekday]}. Скорректируй интервалы перед добавлением.`); return; }
      next.days[lesson.weekday].push(slot);
    }
    setDraft(next); setImportError(''); importing.reset();
  }
  return <Paper withBorder p="lg" radius="md"><Stack>
    <Group justify="space-between"><Title order={4}>Моя неделя</Title><Badge size="xs" variant="light">{query.data.configured ? 'Личное расписание' : 'Шаблон — сохрани после настройки'}</Badge></Group>
    <TextInput label="Часовой пояс" placeholder="Europe/Moscow" value={week.timezone} onChange={(event) => setDraft({ ...week, timezone: event.currentTarget.value })} />
    <TextInput type="date" label="Понедельник первой учебной недели (нечётной)" description="Если не задан, чётность определяется по календарным ISO-неделям." value={week.parity_anchor ?? ''} onChange={(event) => setDraft({ ...week, parity_anchor: event.currentTarget.value || null })} />
    <Text size="xs" c="dimmed">Ameba планирует только в рабочих интервалах. Ночной сон разделяй в 00:00; конец дня — 24:00.</Text>
    <Accordion variant="separated">
      {days.map((day, d) => <Accordion.Item key={day} value={day}><Accordion.Control>{day} · {week.days[d].filter((slot) => slot.kind === 'work').length} рабочих окон</Accordion.Control><Accordion.Panel><Stack gap={6}>
        {week.days[d].map((slot, i) => <Group key={i} gap={6} wrap="wrap">
          <Select w={160} size="xs" data={kinds} value={slot.kind} onChange={(kind) => update(d, i, { kind })} allowDeselect={false} />
          <Select size="xs" w={155} data={patterns} value={slot.week_pattern ?? 'all'} onChange={(week_pattern) => update(d, i, { week_pattern })} allowDeselect={false} />
          <TextInput size="xs" w={130} aria-label="Название интервала" value={slot.title} onChange={(event) => update(d, i, { title: event.currentTarget.value })} maxLength={150} />
          <TextInput size="xs" w={75} aria-label="Начало" value={slot.start} onChange={(event) => update(d, i, { start: event.currentTarget.value })} placeholder="09:00" />
          <TextInput size="xs" w={75} aria-label="Конец" value={slot.end} onChange={(event) => update(d, i, { end: event.currentTarget.value })} placeholder="18:00" />
          <Button size="compact-xs" variant="subtle" color="red" onClick={() => setDraft({ ...week, days: week.days.map((slots, index) => index === d ? slots.filter((_, index) => index !== i) : slots) })}>Убрать</Button>
        </Group>)}
        <Group><Button size="compact-xs" variant="light" onClick={() => setDraft({ ...week, days: week.days.map((slots, index) => index === d ? [...slots, { kind: 'busy', title: '', start: '16:00', end: '17:00' }] : slots) })}>Интервал</Button>
        {d === 0 && <Button size="compact-xs" variant="subtle" onClick={() => setDraft({ ...week, days: week.days.map((slots, index) => index > 0 && index < 5 ? week.days[0].map((slot) => ({ ...slot })) : slots) })}>Повторить пн–пт</Button>}</Group>
      </Stack></Accordion.Panel></Accordion.Item>)}
    </Accordion>
    <Title order={5}>Разовые события</Title>
    {(week.events ?? []).map((event, i) => {
      const edit = (values) => setDraft({ ...week, events: week.events.map((item, index) => index === i ? { ...item, ...values } : item) });
      return <Group key={i} gap={6}>
        <TextInput type="date" size="xs" aria-label="Дата события" value={event.date} onChange={(e) => edit({ date: e.currentTarget.value })} />
        <TextInput size="xs" w={160} placeholder="Название события" value={event.title} onChange={(e) => edit({ title: e.currentTarget.value })} maxLength={150} />
        <Select size="xs" w={140} data={kinds.filter((kind) => kind.value !== 'work')} value={event.kind} onChange={(kind) => edit({ kind })} allowDeselect={false} />
        <TextInput size="xs" w={75} aria-label="Начало события" value={event.start} onChange={(e) => edit({ start: e.currentTarget.value })} />
        <TextInput size="xs" w={75} aria-label="Конец события" value={event.end} onChange={(e) => edit({ end: e.currentTarget.value })} />
        <Button size="compact-xs" variant="subtle" color="red" onClick={() => setDraft({ ...week, events: week.events.filter((_, index) => index !== i) })}>Убрать</Button>
      </Group>;
    })}
    <Button size="compact-xs" variant="light" onClick={() => setDraft({ ...week, events: [...(week.events ?? []), { date: new Date().toLocaleDateString('en-CA'), kind: 'busy', title: '', start: '16:00', end: '17:00', week_pattern: 'all' }] })}>Добавить событие</Button>
    <Button loading={save.isPending} onClick={() => save.mutate()}>Сохранить неделю</Button>
    {save.error && <Alert color="red">{save.error.message}. Проверь часовой пояс, время и пересечения интервалов.</Alert>}
    {save.isSuccess && !draft && <Text size="xs" c="green">Сохранено</Text>}
    {features.context !== false && <Stack gap="xs">
      <Title order={5}>Straus · импорт из ЛК</Title>
      <Select label="Личный кабинет" value={siteId} onChange={setSiteId} data={(sites.data ?? []).map((site) => ({ value: site.id, label: site.name }))} />
      <TextInput label="Ссылка на расписание" value={link} onChange={(event) => setLink(event.currentTarget.value)} />
      <TextInput label="Группа, неделя и другие уточнения" value={notes} onChange={(event) => setNotes(event.currentTarget.value)} maxLength={2000} />
      <Button variant="light" disabled={!siteId || !link} loading={importing.isPending} onClick={() => { setImportError(''); importing.mutate(); }}>Прочитать расписание</Button>
      {importing.isPending && <AgentProgress providers={['context']} active />}
      {importing.error && <Alert color="red">{importing.error.message}</Alert>}
      {importing.data && <Stack gap={5}>
        {importing.data.warnings.map((warning, i) => <Alert key={i} color="yellow">{warning}</Alert>)}
        {importing.data.lessons.map((lesson, i) => <Text key={i} size="xs">{lesson.date ?? days[lesson.weekday]}{!lesson.date && lesson.week_pattern !== 'all' ? ` · ${lesson.week_pattern === 'even' ? 'чётные' : 'нечётные'} недели` : ''} · {lesson.start}–{lesson.end} · {lesson.title}</Text>)}
        <Button variant="light" disabled={!importing.data.lessons.length} onClick={applyImport}>Добавить в черновик недели</Button>
        <Text size="xs" c="dimmed">Проверь интервалы и нажми «Сохранить неделю». Занятия займут рабочие окна только в соответствующую неделю или дату.</Text>
      </Stack>}
      {importError && <Alert color="red">{importError}</Alert>}
      {sites.error && <Text size="xs" c="red">{sites.error.message}</Text>}
    </Stack>}
  </Stack></Paper>;
}
