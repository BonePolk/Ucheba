import AgentProblemReport from './AgentProblemReport';
import { calendarColors as colors, calendarBlockColor } from './calendarColors';
import { useEffect, useRef, useState } from 'react';
import { Alert, Anchor, Badge, Button, Group, Modal, NumberInput, Paper, Select, Stack, Text, Textarea, TextInput, Title, Tooltip, Switch } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { agentRequest, request } from './api';
import AgentProgress from './AgentProgress';
import MonocleBrowser from './LuchebaBrowser';

const names = { task: 'Задача', busy: 'Занятость', sleep: 'Сон', meal: 'Еда', break: 'Отдых', work: 'Рабочее время', personal: 'Личное время' };
const units = [{ value: 'none', label: 'Не повторять' }, { value: 'day', label: 'Дни' }, { value: 'week', label: 'Недели' }, { value: 'month', label: 'Месяцы' }];
const minutes = (time) => Number(time.slice(0, 2)) * 60 + Number(time.slice(3));
const clock = (value) => `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
const dateKey = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
const asDate = (value) => new Date(`${value}T12:00:00`);
function addDays(value, amount) { const date = asDate(value); date.setDate(date.getDate() + amount); return dateKey(date); }
function monday(value) { const date = asDate(value); return addDays(value, -((date.getDay() + 6) % 7)); }
function monthStart(value) { const date = asDate(value); date.setDate(1); return dateKey(date); }

// Пересекающиеся события рисуем рядом, а рабочие окна остаются фоном.
function layout(events) {
  const groups = [];
  for (const event of [...events].sort((a, b) => a.start.localeCompare(b.start))) {
    let group = groups.at(-1);
    if (!group || event.start >= group.end) { group = { end: event.end, events: [], lanes: [] }; groups.push(group); }
    let lane = group.lanes.findIndex((end) => end <= event.start);
    if (lane < 0) lane = group.lanes.length;
    group.lanes[lane] = event.end;
    group.end = group.end > event.end ? group.end : event.end;
    group.events.push({ ...event, lane });
  }
  return groups.flatMap((group) => group.events.map((event) => ({ ...event, columns: group.lanes.length })));
}

export default function Calendar({ features = {} }) {
  const client = useQueryClient();
  const [date, setDate] = useState(() => dateKey(new Date()));
  const [view, setView] = useState('week');
  const [showAmeba, setShowAmeba] = useState(true);
  const [editor, setEditor] = useState(null);
  const [error, setError] = useState('');
  const [command, setCommand] = useState('');
  const [timezoneDraft, setTimezoneDraft] = useState(null);
  const viewport = useRef(null);
  const addedTask = useRef(false);
  const start = view === 'week' ? monday(date) : monday(monthStart(date));
  const end = addDays(start, view === 'week' ? 6 : 41);
  const calendar = useQuery({ queryKey: ['calendar', start, end], queryFn: () => request(`/calendar?start=${start}&end=${end}`), refetchInterval: 5000 });
  const tasks = useQuery({ queryKey: ['calendar-tasks'], queryFn: () => request('/tasks') });
  const status = useQuery({ queryKey: ['straus-status'], queryFn: () => request('/integrations/context/schedule/import/status'), enabled: features.straus !== false, refetchInterval: 1000 });
  const refresh = () => Promise.all([
    client.invalidateQueries({ queryKey: ['calendar'] }),
    client.invalidateQueries({ queryKey: ['jev-ranking'] }),
    client.invalidateQueries({ queryKey: ['straus-status'] }),
  ]);
  const save = useMutation({ mutationFn: (config) => request('/calendar', { method: 'PUT', body: JSON.stringify(config) }), onSuccess: async () => { setEditor(null); setError(''); setTimezoneDraft(null); await refresh(); } });
  const editAgent = useMutation({ mutationFn: () => agentRequest('/calendar/agent', { method: 'POST', body: JSON.stringify({ instruction: command }) }), onSettled: refresh });
  const stop = useMutation({ mutationFn: () => request('/integrations/context/schedule/import/stop', { method: 'POST' }), onSuccess: async () => { editAgent.reset(); await refresh(); } });
  const busy = Boolean(status.data?.running || editAgent.isPending || stop.isPending);
  useEffect(() => { if (viewport.current && view === 'week') viewport.current.scrollTop = 7 * 44; }, [start, view]);
  const events = (calendar.data?.events ?? []).filter((event) => event.source !== 'ameba' || (showAmeba && features.ameba !== false));
  const taskList = (tasks.data ?? []).filter((task) => task.status !== 'done');
  const config = calendar.data?.config;
  const days = Array.from({ length: view === 'week' ? 7 : 42 }, (_, index) => addDays(start, index));

  useEffect(() => {
    const id = Number(new URLSearchParams(window.location.search).get('add_task'));
    const task = taskList.find((item) => item.id === id);
    if (!config || !task || addedTask.current) return;
    addedTask.current = true;
    setEditor({ id: crypto.randomUUID(), date: calendar.data.today, start: '09:00', end: '10:00',
      title: task.title.slice(0, 150), summary: (task.description ?? '').slice(0, 500), kind: 'task', task_id: task.id,
      repeat_unit: 'none', repeat_interval: 1, repeat_count: null });
  }, [config, tasks.data]);

  function create(day, minute = 9 * 60) {
    if (!config) return;
    save.reset();
    setError('');
    setEditor({ id: crypto.randomUUID(), date: day, start: clock(minute), end: clock(Math.min(1440, minute + 60)), title: '', summary: '', kind: 'busy', task_id: null, repeat_unit: 'none', repeat_interval: 1, repeat_count: null });
  }
  function edit(event, day = event.date, startTime = event.start) {
    if (!config) return;
    if (event.source === 'ameba') {
      setError('Это временный план Ameba. Он пересоздаётся при её следующем запуске; постоянные события редактируются отдельно.');
      return;
    }
    save.reset();
    const original = config.events.find((item) => item.id === event.id);
    const duration = minutes(event.end) - minutes(event.start);
    setEditor(original ? { ...original, date: day === event.date ? original.date : day, start: startTime, end: clock(Math.min(1440, minutes(startTime) + duration)) } : {
      id: crypto.randomUUID(), date: day, start: startTime, end: clock(Math.min(1440, minutes(startTime) + duration)),
      title: event.title, summary: event.summary ?? '', task_id: event.task_id ?? null, kind: event.kind,
      repeat_unit: 'none', repeat_interval: 1, repeat_count: null,
    });
    setError('');
  }
  function saveEvent() {
    if (!editor.title.trim() || !editor.date || editor.end <= editor.start || (editor.kind === 'task' && !editor.task_id)) {
      setError('Укажи название, дату, корректное время и задачу для блока работы.'); return;
    }
    const event = { ...editor };
    save.mutate({ ...config, events: [...config.events.filter((item) => item.id !== event.id), event] });
  }
  function shift(direction) {
    if (view === 'week') setDate(addDays(date, direction * 7));
    else { const next = asDate(date); next.setDate(1); next.setMonth(next.getMonth() + direction); setDate(dateKey(next)); }
  }
  function renderEvent(event) {
    return <Tooltip key={`${event.id}:${event.date}`} multiline w={260} label={<>{event.start}–{event.end} · {event.title}{event.summary ? <><br />{event.summary}</> : null}</>}>
      <button className={`calendar-event ${event.source === 'ameba' ? 'calendar-ameba-event' : ''}`} style={{ top: minutes(event.start) / 1440 * 1056, height: (minutes(event.end) - minutes(event.start)) / 1440 * 1056, padding: minutes(event.end) - minutes(event.start) < 15 ? 0 : undefined, borderWidth: minutes(event.end) - minutes(event.start) < 3 ? 0 : undefined, left: `calc(${event.lane / event.columns * 100}% + 2px)`, width: `calc(${100 / event.columns}% - 4px)`, background: calendarBlockColor(event.kind, event.source === 'ameba'), borderColor: colors[event.kind] }}
        draggable={event.source === 'calendar'} onDragStart={(drag) => drag.dataTransfer.setData('text/plain', event.id)}
        onClick={(click) => { click.stopPropagation(); edit(event); }}>
        <span>{event.start} {event.title}{event.source === 'ameba' ? ' · Ameba' : ''}</span>
        {event.summary && <small>{event.summary}</small>}
      </button>
    </Tooltip>;
  }

  return <Paper withBorder p="md" radius="md"><Stack gap="sm">
    <Group justify="space-between">
      <Group gap="xs"><Title order={3}>Календарь</Title><Badge variant="light">{calendar.data?.timezone ?? '…'}</Badge></Group>
      <Group gap="xs"><Button size="xs" variant="default" disabled={!config} onClick={() => create(date)}>+ Событие</Button><Button size="xs" variant="light" onClick={() => setDate(calendar.data?.today ?? dateKey(new Date()))}>Сегодня</Button><Button size="xs" variant="subtle" aria-label="Назад" onClick={() => shift(-1)}>‹</Button><Button size="xs" variant="subtle" aria-label="Вперёд" onClick={() => shift(1)}>›</Button><Select size="xs" w={110} value={view} onChange={setView} allowDeselect={false} data={[{ value: 'week', label: 'Неделя' }, { value: 'month', label: 'Месяц' }]} /></Group>
    </Group>
    <Text fw={500}>{asDate(view === 'month' ? monthStart(date) : start).toLocaleDateString('ru-RU', view === 'month' ? { month: 'long', year: 'numeric' } : { day: 'numeric', month: 'long' })}{view === 'week' && ` — ${asDate(end).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })}`}</Text>
    {features.ameba !== false && <Group gap="xs"><Switch size="xs" label="Временный план Ameba" checked={showAmeba} onChange={(event) => setShowAmeba(event.currentTarget.checked)} /><Text size="xs" c="dimmed">Сбрасывается при её следующем запуске. Постоянные события сохраняются.</Text></Group>}
    {calendar.error && <Alert color="red">{calendar.error.message}</Alert>}
    {error && !editor && <Alert color="yellow">{error}</Alert>}
    {view === 'week' ? <div className="calendar-scroll" ref={viewport}><div className="calendar-week">
      <div className="calendar-week-header"><div />{days.map((day) => <div key={day} className={day === calendar.data?.today ? 'calendar-today' : ''}>{asDate(day).toLocaleDateString('ru-RU', { weekday: 'short', day: 'numeric', month: 'short' })}</div>)}</div>
      <div className="calendar-week-body"><div className="calendar-hours">{Array.from({ length: 24 }, (_, hour) => <span key={hour} style={{ top: hour * 44 }}>{String(hour).padStart(2, '0')}:00</span>)}</div>
        {days.map((day) => <div key={day} className="calendar-day" onClick={(click) => { const box = click.currentTarget.getBoundingClientRect(); create(day, Math.min(1410, Math.floor((click.clientY - box.top) / 1056 * 96) * 15)); }}
          onDragOver={(drag) => drag.preventDefault()} onDrop={(drag) => { drag.preventDefault(); const event = events.find((item) => item.id === drag.dataTransfer.getData('text/plain')); if (!event || event.source !== 'calendar') return; const box = drag.currentTarget.getBoundingClientRect(); const minute = Math.min(1410, Math.floor((drag.clientY - box.top) / 1056 * 96) * 15); edit(event, day, clock(minute)); }}>
          {events.filter((event) => event.date === day && event.kind === 'work').map((event) => <button type="button" key={event.id} className="calendar-work" onClick={(click) => { click.stopPropagation(); edit(event); }} style={{ top: minutes(event.start) / 1440 * 1056, height: (minutes(event.end) - minutes(event.start)) / 1440 * 1056 }}>{event.title}</button>)}
          {layout(events.filter((event) => event.date === day && event.kind !== 'work')).map(renderEvent)}
        </div>)}
      </div>
    </div></div> : <div className="calendar-month">{days.map((day) => {
      const items = events.filter((event) => event.date === day && event.kind !== 'work');
      return <div key={day} className={`calendar-month-day ${day === calendar.data?.today ? 'calendar-today' : ''}`}><button className="calendar-date" onClick={() => create(day)}>{asDate(day).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', weekday: 'short' })}</button>
        {items.slice(0, 4).map((event) => <button key={event.id} className={`calendar-month-event ${event.source === 'ameba' ? 'calendar-ameba-event' : ''}`} style={{ borderLeftColor: colors[event.kind], background: calendarBlockColor(event.kind, event.source === 'ameba') }} onClick={() => edit(event)}>{event.start} {event.title}{event.source === 'ameba' ? ' · Ameba' : ''}</button>)}
        {items.length > 4 && <Button variant="subtle" size="compact-xs" onClick={() => { setDate(day); setView('week'); }}>Ещё {items.length - 4}</Button>}
      </div>;
    })}</div>}
    <Text size="xs" c="dimmed">Нажми на свободное время, чтобы добавить событие. Перенос открывает подтверждение; для повторяющегося события меняется вся серия.</Text>
    <Group gap="xs">{['task', 'busy', 'sleep', 'meal', 'break', 'work'].map((kind) => <Badge key={kind} size="xs" color={colors[kind]} variant="light">{names[kind]}</Badge>)}</Group>
    {config && <Group align="end"><TextInput label="Часовой пояс" placeholder="Europe/Moscow" value={timezoneDraft ?? config.timezone} onChange={(change) => setTimezoneDraft(change.currentTarget.value)} /><Button size="xs" variant="light" disabled={timezoneDraft === null || save.isPending} onClick={() => save.mutate({ ...config, timezone: timezoneDraft })}>Сохранить часовой пояс</Button></Group>}
    {config && features.straus !== false && <>
      <Textarea label="Поручение Straus" placeholder="Например: найди расписание моей группы в ЛК и добавь пары на следующие две недели" value={command} onChange={(change) => setCommand(change.currentTarget.value)} autosize minRows={2} maxLength={12000} disabled={busy} />
      <Group><Button variant="light" size="xs" disabled={!command.trim() || busy || status.isPending || status.isError} onClick={() => { stop.reset(); editAgent.mutate(); }}>Изменить календарь</Button>{status.data?.running && <Button size="xs" color="red" variant="light" loading={stop.isPending} onClick={() => stop.mutate()}>Остановить Straus</Button>}<Anchor size="xs" href="/profile">Личные кабинеты и настройки агента</Anchor></Group>
      {editAgent.isSuccess && (editAgent.data?.changed ? <Text size="xs" c="green">Календарь обновлён.</Text> : <Alert color={editAgent.data?.status === 'blocked' ? 'yellow' : 'gray'} title={editAgent.data?.status === 'blocked' ? 'Изменения не внесены' : 'Изменения не нужны'}>{editAgent.data?.reason}</Alert>)}
      {(editAgent.error || (editAgent.isSuccess && editAgent.data?.status === 'blocked')) && <AgentProblemReport agent="Straus" toolName="calendar_edit" />}
      {status.data?.error && !busy && <Alert color="yellow">{status.data.error}</Alert>}
      <AgentProgress providers={['straus']} active={busy} /><MonocleBrowser active={busy} />
    </>}
    {(save.error || editAgent.error || stop.error) && !editor && <Alert color="red">{(save.error || editAgent.error || stop.error).message}</Alert>}
    <Modal opened={Boolean(editor)} onClose={() => setEditor(null)} title="Событие" centered>
      {editor && <Stack gap="sm">
        <TextInput label="Название" value={editor.title} maxLength={150} onChange={(change) => setEditor({ ...editor, title: change.currentTarget.value })} />
        <Textarea label="Краткая сводка" value={editor.summary} maxLength={500} autosize minRows={2} onChange={(change) => setEditor({ ...editor, summary: change.currentTarget.value })} />
        <Select label="Тип" allowDeselect={false} value={editor.kind} data={Object.entries(names).map(([value, label]) => ({ value, label }))} onChange={(kind) => setEditor({ ...editor, kind, task_id: kind === 'task' ? editor.task_id : null })} />
        {editor.kind === 'task' && <Select searchable label="Задача" placeholder="Выбери задачу" value={editor.task_id ? String(editor.task_id) : null} data={taskList.map((task) => ({ value: String(task.id), label: task.title }))} onChange={(value) => { const task = taskList.find((item) => item.id === Number(value)); setEditor({ ...editor, task_id: value ? Number(value) : null, title: task?.title.slice(0, 150) ?? editor.title, summary: (task?.description ?? '').slice(0, 500) }); }} />}
        {editor.task_id && <Anchor size="sm" href={`/?task=${editor.task_id}`}>Открыть полную задачу</Anchor>}
        <TextInput type="date" label="Дата начала" value={editor.date} onChange={(change) => setEditor({ ...editor, date: change.currentTarget.value })} />
        <Group grow><TextInput label="Начало" placeholder="09:00" value={editor.start} onChange={(change) => setEditor({ ...editor, start: change.currentTarget.value })} /><TextInput label="Конец" placeholder="10:00" value={editor.end} onChange={(change) => setEditor({ ...editor, end: change.currentTarget.value })} /></Group>
        <Select label="Повторение" value={editor.repeat_unit} onChange={(repeat_unit) => setEditor({ ...editor, repeat_unit, week_pattern: repeat_unit === 'week' ? (editor.week_pattern ?? 'all') : 'all' })} data={units} allowDeselect={false} />
        {editor.repeat_unit !== 'none' && <><Group grow><NumberInput label="Каждые N дней / недель / месяцев" min={1} max={366} value={editor.repeat_interval} onChange={(value) => setEditor({ ...editor, repeat_interval: typeof value === 'number' ? value : 1 })} /><NumberInput label="Количество повторений" placeholder="Без ограничения" min={1} max={10000} value={editor.repeat_count ?? ''} onChange={(value) => setEditor({ ...editor, repeat_count: typeof value === 'number' ? value : null })} /></Group><Text size="xs" c="dimmed">Количество включает первое событие. Меняется вся серия. В коротком месяце используется последний день.</Text></>}
        {editor.repeat_unit === 'week' && <Select label="Учебные недели" value={editor.week_pattern ?? 'all'} allowDeselect={false} data={[{ value: 'all', label: 'Все недели' }, { value: 'even', label: 'Чётные' }, { value: 'odd', label: 'Нечётные' }]} onChange={(week_pattern) => setEditor({ ...editor, week_pattern })} />}
        {editor.repeat_unit === 'week' && editor.week_pattern && editor.week_pattern !== 'all' && <TextInput type="date" label="Понедельник первой нечётной недели" description="Не задан — используется ISO-чётность." value={editor.parity_anchor ?? ''} onChange={(change) => setEditor({ ...editor, parity_anchor: change.currentTarget.value || null })} />}
        {(error || save.error) && <Alert color="red">{error || save.error.message}</Alert>}
        <Group><Button loading={save.isPending} onClick={saveEvent}>Сохранить</Button>{config?.events.some((event) => event.id === editor.id) && <Button color="red" variant="subtle" disabled={save.isPending} onClick={() => save.mutate({ ...config, events: config.events.filter((event) => event.id !== editor.id) })}>Удалить {editor.repeat_unit !== 'none' ? 'серию' : 'событие'}</Button>}</Group>
      </Stack>}
    </Modal>
  </Stack></Paper>;
}
