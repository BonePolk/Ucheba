import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Bot,
  ChevronLeft,
  ChevronRight,
  Layers,
  PanelRight,
  Plus,
  Square,
} from 'lucide-react';
import { agentRequest, request } from './api';
import { calendarColors as colors, calendarBlockColor } from './calendarColors';
import AgentProblemReport from './AgentProblemReport';
import AgentProgress from './AgentProgress';
import { useMediaQuery } from '@/lib/utils';
import { SlidePanel } from '@/lib/motion';
import { Button, IconButton } from '@/components/ui/button';
import { Drawer, Modal } from '@/components/ui/dialog';
import { Field, Input, Textarea } from '@/components/ui/input';
import { Combobox, SelectField } from '@/components/ui/select';
import { Notice, PageHeader, StatusIndicator } from '@/components/ui/surface';
import { SegmentedControl } from '@/components/ui/tabs';
import { HintTooltip } from '@/components/ui/tooltip';

const names = {
  task: 'Задача',
  busy: 'Занятость',
  sleep: 'Сон',
  meal: 'Еда',
  break: 'Отдых',
  work: 'Рабочее время',
  personal: 'Личное время',
};

const units = [
  { value: 'none', label: 'Не повторять' },
  { value: 'day', label: 'Дни' },
  { value: 'week', label: 'Недели' },
  { value: 'month', label: 'Месяцы' },
];

const minutes = (time) => Number(time.slice(0, 2)) * 60 + Number(time.slice(3));
const eventId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, '0')).join('');
const clock = (value) => `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
const dateKey = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
const asDate = (value) => new Date(`${value}T12:00:00`);
function addDays(value, amount) {
  const date = asDate(value);
  date.setDate(date.getDate() + amount);
  return dateKey(date);
}
function monday(value) {
  const date = asDate(value);
  return addDays(value, -((date.getDay() + 6) % 7));
}
function monthStart(value) {
  const date = asDate(value);
  date.setDate(1);
  return dateKey(date);
}

// Пересекающиеся события рисуем рядом, а рабочие окна остаются фоном.
function layout(events) {
  const groups = [];
  for (const event of [...events].sort((a, b) => a.start.localeCompare(b.start))) {
    let group = groups.at(-1);
    if (!group || event.start >= group.end) {
      group = { end: event.end, events: [], lanes: [] };
      groups.push(group);
    }
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
  const compact = useMediaQuery('(max-width: 767px)', typeof window !== 'undefined' ? window.innerWidth < 768 : false);
  const [date, setDate] = useState(() => dateKey(new Date()));
  const [selectedDay, setSelectedDay] = useState(() => dateKey(new Date()));
  const [view, setView] = useState('week');
  const [showAmeba, setShowAmeba] = useState(true);
  const [dayPanelOpen, setDayPanelOpen] = useState(false);
  const [strausOpen, setStrausOpen] = useState(false);
  const [editor, setEditor] = useState(null);
  const [error, setError] = useState('');
  const [command, setCommand] = useState('');
  const viewport = useRef(null);
  const addedTask = useRef(false);

  const start = view === 'week' ? monday(date) : monday(monthStart(date));
  const end = addDays(start, view === 'week' ? 6 : 41);

  const calendar = useQuery({
    queryKey: ['calendar', start, end],
    queryFn: () => request(`/calendar?start=${start}&end=${end}`),
    refetchInterval: 5000,
  });
  const tasks = useQuery({ queryKey: ['calendar-tasks'], queryFn: () => request('/tasks') });
  const ranking = useQuery({
    queryKey: ['ameba-ranking'],
    queryFn: () => request('/integrations/ameba/ranking'),
    enabled: features.ameba !== false,
    refetchInterval: 15000,
  });
  const status = useQuery({
    queryKey: ['straus-status'],
    queryFn: () => request('/integrations/monocle/schedule/import/status'),
    enabled: features.straus !== false,
    refetchInterval: 1000,
  });

  const refresh = () => Promise.all([
    client.invalidateQueries({ queryKey: ['calendar'] }),
    client.invalidateQueries({ queryKey: ['ameba-ranking'] }),
    client.invalidateQueries({ queryKey: ['straus-status'] }),
  ]);

  const save = useMutation({
    mutationFn: (config) => request('/calendar', { method: 'PUT', body: JSON.stringify(config) }),
    onSuccess: async () => {
      setEditor(null);
      setError('');
      await refresh();
    },
  });
  const editAgent = useMutation({
    mutationFn: () => agentRequest('/calendar/agent', { method: 'POST', body: JSON.stringify({ instruction: command }) }),
    onSettled: refresh,
  });
  const stop = useMutation({
    mutationFn: () => request('/integrations/monocle/schedule/import/stop', { method: 'POST' }),
    onSuccess: async () => {
      editAgent.reset();
      await refresh();
    },
  });

  const busy = Boolean(status.data?.running || editAgent.isPending || stop.isPending);
  useEffect(() => {
    if (viewport.current && view === 'week') viewport.current.scrollTop = 7 * 44;
  }, [start, view]);

  const allEvents = calendar.data?.events ?? [];
  const permanentEvents = allEvents.filter((event) => event.source !== 'ameba');
  const tempEvents = features.ameba !== false && showAmeba ? allEvents.filter((event) => event.source === 'ameba') : [];
  const visibleEvents = [...permanentEvents, ...tempEvents];

  const amebaDailyMap = new Map(
    (features.ameba !== false && showAmeba && !ranking.data?.plan_reset
      ? (ranking.data?.schedule?.daily_allocations ?? [])
      : []
    ).map((d) => [d.date, d]),
  );

  const taskList = (tasks.data ?? []).filter((task) => task.status !== 'done');
  const config = calendar.data?.config;
  const days = Array.from({ length: view === 'week' ? 7 : 42 }, (_, index) => addDays(start, index));

  useEffect(() => {
    const id = Number(new URLSearchParams(window.location.search).get('add_task'));
    const task = taskList.find((item) => item.id === id);
    if (!config || !task || addedTask.current) return;
    addedTask.current = true;
    setEditor({
      id: eventId(),
      date: calendar.data.today,
      start: '09:00',
      end: '10:00',
      title: task.title.slice(0, 150),
      summary: (task.description ?? '').slice(0, 500),
      kind: 'task',
      task_id: task.id,
      repeat_unit: 'none',
      repeat_interval: 1,
      repeat_count: null,
    });
  }, [config, tasks.data]);

  function create(day, minute = 9 * 60) {
    if (!config) return;
    save.reset();
    setError('');
    setSelectedDay(day);
    setEditor({
      id: eventId(),
      date: day,
      start: clock(minute),
      end: clock(Math.min(1440, minute + 60)),
      title: '',
      summary: '',
      kind: 'busy',
      task_id: null,
      repeat_unit: 'none',
      repeat_interval: 1,
      repeat_count: null,
    });
  }

  function edit(event, day = event.date, startTime = event.start) {
    if (!config || event.source === 'ameba') return;
    save.reset();
    const original = config.events.find((item) => item.id === event.id);
    const duration = minutes(event.end) - minutes(event.start);
    setEditor(
      original
        ? { ...original, date: day === event.date ? original.date : day, start: startTime, end: clock(Math.min(1440, minutes(startTime) + duration)) }
        : {
          id: eventId(),
          date: day,
          start: startTime,
          end: clock(Math.min(1440, minutes(startTime) + duration)),
          title: event.title,
          summary: event.summary ?? '',
          task_id: event.task_id ?? null,
          kind: event.kind,
          repeat_unit: 'none',
          repeat_interval: 1,
          repeat_count: null,
        },
    );
    setError('');
  }

  function saveEvent() {
    if (!editor.title.trim() || !editor.date || editor.end <= editor.start || (editor.kind === 'task' && !editor.task_id)) {
      setError('Укажите название, дату, корректное время и задачу для блока работы.');
      return;
    }
    const event = { ...editor };
    save.mutate({ ...config, events: [...config.events.filter((item) => item.id !== event.id), event] });
  }

  function shift(direction) {
    if (view === 'week') setDate(addDays(date, direction * 7));
    else {
      const next = asDate(date);
      next.setDate(1);
      next.setMonth(next.getMonth() + direction);
      setDate(dateKey(next));
    }
  }

  function renderEvent(event) {
    const isTemporary = event.source === 'ameba';
    const durMin = minutes(event.end) - minutes(event.start);
    return (
      <HintTooltip
        key={`${event.id}:${event.date}:${event.source ?? 'cal'}`}
        label={<>{event.start}–{event.end} · {event.title}{isTemporary ? ' (временный слой Ameba)' : ''}{event.summary ? `\n${event.summary}` : ''}</>}
      >
        <button
          type="button"
          className={`calendar-event ${isTemporary ? 'is-temporary' : ''}`}
          style={{
            top: (minutes(event.start) / 1440) * 1056,
            height: (durMin / 1440) * 1056,
            padding: durMin < 15 ? 0 : undefined,
            borderWidth: !isTemporary && durMin < 3 ? 0 : undefined,
            left: `calc(${(event.lane / event.columns) * 100}% + 2px)`,
            width: `calc(${100 / event.columns}% - 4px)`,
            background: calendarBlockColor(event.kind, isTemporary),
            borderColor: isTemporary ? '#B98CE8' : colors[event.kind],
          }}
          draggable={!isTemporary}
          onDragStart={(drag) => {
            if (isTemporary) return;
            drag.dataTransfer.setData('text/plain', event.id);
          }}
          onClick={(click) => {
            click.stopPropagation();
            if (!isTemporary) edit(event);
          }}
        >
          <span>{event.start} {event.title}</span>
          {event.summary && <small>{event.summary}</small>}
        </button>
      </HintTooltip>
    );
  }

  const periodTitle = `${asDate(view === 'month' ? monthStart(date) : start).toLocaleDateString('ru-RU', view === 'month' ? { month: 'long', year: 'numeric' } : { day: 'numeric', month: 'long' })}${view === 'week' ? ` — ${asDate(end).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })}` : ''}`;
  const selectedDayEvents = permanentEvents.filter((event) => event.date === selectedDay).sort((a, b) => a.start.localeCompare(b.start));
  const selectedDayAmeba = amebaDailyMap.get(selectedDay);

  return (
    <div className="flex flex-col gap-3">
      <PageHeader
        title="Календарь"
        subtitle={periodTitle}
        actions={(
          <>
            <SegmentedControl
              ariaLabel="Вид календаря"
              value={view}
              onChange={setView}
              options={[{ value: 'week', label: 'Неделя' }, { value: 'month', label: 'Месяц' }]}
            />
            <div className="inline-flex items-center border border-[#303030] bg-[#111111]">
              <IconButton size="icon" variant="ghost" aria-label="Назад" onClick={() => shift(-1)}>
                <ChevronLeft className="h-4 w-4" />
              </IconButton>
              <Button
                size="default"
                variant="ghost"
                className="border-x border-[#262626]"
                onClick={() => {
                  const t = calendar.data?.today ?? dateKey(new Date());
                  setDate(t);
                  setSelectedDay(t);
                }}
              >
                Сегодня
              </Button>
              <IconButton size="icon" variant="ghost" aria-label="Вперёд" onClick={() => shift(1)}>
                <ChevronRight className="h-4 w-4" />
              </IconButton>
            </div>

            {features.ameba !== false && (
              <Button
                variant={showAmeba ? 'accent-subtle' : 'default'}
                leftIcon={<Layers className="h-3.5 w-3.5" />}
                onClick={() => setShowAmeba(!showAmeba)}
                aria-pressed={showAmeba}
              >
                Слой Ameba
              </Button>
            )}

            <Button
              variant={dayPanelOpen ? 'accent-subtle' : 'default'}
              leftIcon={<PanelRight className="h-3.5 w-3.5" />}
              onClick={() => setDayPanelOpen(!dayPanelOpen)}
              aria-expanded={dayPanelOpen}
            >
              День
            </Button>

            {features.straus !== false && (
              <Button
                variant={busy ? 'accent-subtle' : 'default'}
                leftIcon={<Bot className="h-3.5 w-3.5" />}
                onClick={() => setStrausOpen(true)}
              >
                {busy ? 'Straus работает…' : 'Поручить Straus'}
              </Button>
            )}

            <Button variant="primary" leftIcon={<Plus className="h-3.5 w-3.5" />} disabled={!config} onClick={() => create(selectedDay)}>
              Событие
            </Button>
          </>
        )}
      />

      {/* Compact live Straus banner when working in background */}
      {busy && !strausOpen && (
        <div className="border border-[#543476] bg-[#16101D] px-3 py-1.5 flex flex-wrap items-center justify-between gap-2 text-[12px]">
          <div className="flex items-center gap-2">
            <StatusIndicator tone="active" pulse />
            <span>Straus обновляет расписание в фоне…</span>
          </div>
          <div className="flex items-center gap-1.5">
            <Button size="xs" variant="outline" onClick={() => setStrausOpen(true)}>Открыть ход работы</Button>
            <Button size="xs" variant="danger" leftIcon={<Square className="h-3 w-3" />} loading={stop.isPending} onClick={() => stop.mutate()}>
              Остановить
            </Button>
          </div>
        </div>
      )}

      {calendar.error && <Notice tone="error">{calendar.error.message}</Notice>}
      {error && !editor && <Notice tone="warning">{error}</Notice>}
      {(save.error || editAgent.error || stop.error) && !editor && !strausOpen && (
        <Notice tone="error">{(save.error || editAgent.error || stop.error).message}</Notice>
      )}

      {/* Calendar grid + optional selected day panel */}
      <div className={`grid gap-3 items-start ${dayPanelOpen ? 'grid-cols-1 xl:grid-cols-[minmax(0,1fr)_270px]' : 'grid-cols-1'}`}>
        <div className="min-w-0 flex flex-col gap-2">
          {compact ? (
            <div className="flex flex-col gap-2">
              {days.map((day) => {
                const dayEvents = visibleEvents.filter((event) => event.date === day).sort((a, b) => a.start.localeCompare(b.start));
                const dayAmeba = amebaDailyMap.get(day);
                return (
                  <section
                    key={day}
                    className={`border border-[#303030] bg-[#111111] p-2.5 flex gap-3 ${day === calendar.data?.today ? 'border-l-2 border-l-[#7F49B4]' : ''}`}
                  >
                    <button
                      type="button"
                      onClick={() => { setSelectedDay(day); setDayPanelOpen(true); }}
                      className="w-12 shrink-0 text-center cursor-pointer"
                    >
                      <div className={`text-[18px] font-bold leading-none ${day === calendar.data?.today ? 'text-[#DFC4F8]' : 'text-[#EAEAEA]'}`}>
                        {asDate(day).toLocaleDateString('ru-RU', { day: 'numeric' })}
                      </div>
                      <div className="text-[11px] text-[#929292] mt-1">
                        {asDate(day).toLocaleDateString('ru-RU', { weekday: 'short', month: 'short' })}
                      </div>
                    </button>

                    <div className="flex-1 min-w-0 flex flex-col gap-1.5">
                      {dayAmeba?.allocated_minutes > 0 && (
                        <a href="/ameba" className="border border-dashed border-[#7F49B4] bg-[#7F49B4]/12 px-2 py-1 text-[11px] text-[#DFC4F8] flex items-center justify-between gap-2">
                          <span>Ameba · {dayAmeba.tasks?.map((t) => t.title).join(', ')}</span>
                          <span className="tabular-nums shrink-0">{Math.round((dayAmeba.allocated_minutes / 60) * 10) / 10} ч</span>
                        </a>
                      )}
                      {dayEvents.map((event) => {
                        const isTemp = event.source === 'ameba';
                        return (
                          <button
                            type="button"
                            key={`${event.id}:${event.source ?? 'cal'}`}
                            onClick={() => { if (!isTemp) edit(event); }}
                            className={`text-left px-2.5 py-1.5 border-l-2 text-[#EAEAEA] text-[12px] ${isTemp ? 'border border-dashed border-[#7F49B4] cursor-default' : 'cursor-pointer'}`}
                            style={{ borderLeftColor: colors[event.kind], background: calendarBlockColor(event.kind, isTemp) }}
                          >
                            <div className="text-[11px] opacity-80 tabular-nums">{event.start}–{event.end}</div>
                            <div className="font-medium break-words">{event.title}</div>
                            {event.summary && <div className="text-[11px] opacity-80">{event.summary}</div>}
                          </button>
                        );
                      })}
                      <button
                        type="button"
                        onClick={() => create(day)}
                        className="text-left border border-dashed border-[#303030] px-2.5 py-1.5 text-[12px] text-[#929292] hover:text-[#EAEAEA] hover:bg-[#191919] cursor-pointer"
                      >
                        + Событие
                      </button>
                    </div>
                  </section>
                );
              })}
            </div>
          ) : view === 'week' ? (
            <div className="calendar-scroll" ref={viewport}>
              <div className="calendar-week">
                <div className="calendar-week-header">
                  <div />
                  {days.map((day) => {
                    const dayAmeba = amebaDailyMap.get(day);
                    return (
                      <div
                        key={day}
                        onClick={() => { setSelectedDay(day); setDayPanelOpen(true); }}
                        className={`cursor-pointer select-none ${day === calendar.data?.today ? 'calendar-today' : ''} ${selectedDay === day ? 'underline decoration-[#7F49B4] underline-offset-4' : ''}`}
                      >
                        <div>{asDate(day).toLocaleDateString('ru-RU', { weekday: 'short', day: 'numeric', month: 'short' })}</div>
                        {dayAmeba?.allocated_minutes > 0 && (
                          <div className="mt-0.5 text-[10px] border border-dashed border-[#7F49B4]/70 bg-[#7F49B4]/12 text-[#DFC4F8] px-1 truncate tabular-nums">
                            {Math.round((dayAmeba.allocated_minutes / 60) * 10) / 10}ч
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>

                <div className="calendar-week-body">
                  <div className="calendar-hours">
                    {Array.from({ length: 24 }, (_, hour) => (
                      <span key={hour} style={{ top: hour * 44 }}>{String(hour).padStart(2, '0')}:00</span>
                    ))}
                  </div>
                  {days.map((day) => (
                    <div
                      key={day}
                      className="calendar-day"
                      onClick={(click) => {
                        const box = click.currentTarget.getBoundingClientRect();
                        create(day, Math.min(1410, Math.floor(((click.clientY - box.top) / 1056) * 96) * 15));
                      }}
                      onDragOver={(drag) => drag.preventDefault()}
                      onDrop={(drag) => {
                        drag.preventDefault();
                        const event = permanentEvents.find((item) => item.id === drag.dataTransfer.getData('text/plain'));
                        if (!event) return;
                        const box = drag.currentTarget.getBoundingClientRect();
                        const minute = Math.min(1410, Math.floor(((drag.clientY - box.top) / 1056) * 96) * 15);
                        edit(event, day, clock(minute));
                      }}
                    >
                      {visibleEvents
                        .filter((event) => event.date === day && event.kind === 'work')
                        .map((event) => (
                          <button
                            type="button"
                            key={`${event.id}:${event.source ?? 'cal'}`}
                            className="calendar-work"
                            onClick={(click) => {
                              click.stopPropagation();
                              if (event.source !== 'ameba') edit(event);
                            }}
                            style={{
                              top: (minutes(event.start) / 1440) * 1056,
                              height: ((minutes(event.end) - minutes(event.start)) / 1440) * 1056,
                            }}
                          >
                            {event.title}
                          </button>
                        ))}
                      {layout(visibleEvents.filter((event) => event.date === day && event.kind !== 'work')).map(renderEvent)}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="calendar-month">
              {days.map((day) => {
                const items = visibleEvents.filter((event) => event.date === day && event.kind !== 'work');
                const dayAmeba = amebaDailyMap.get(day);
                return (
                  <div key={day} className={`calendar-month-day ${day === calendar.data?.today ? 'calendar-today' : ''}`}>
                    <div className="flex items-center justify-between gap-1">
                      <button type="button" className="calendar-date" onClick={() => create(day)}>
                        {asDate(day).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', weekday: 'short' })}
                      </button>
                      {dayAmeba?.allocated_minutes > 0 && (
                        <button
                          type="button"
                          onClick={() => { setSelectedDay(day); setDayPanelOpen(true); }}
                          className="text-[10px] px-1 border border-dashed border-[#7F49B4] text-[#DFC4F8] shrink-0 cursor-pointer"
                        >
                          {Math.round((dayAmeba.allocated_minutes / 60) * 10) / 10}ч
                        </button>
                      )}
                    </div>
                    {items.slice(0, 4).map((event) => {
                      const isTemp = event.source === 'ameba';
                      return (
                        <button
                          type="button"
                          key={`${event.id}:${event.source ?? 'cal'}`}
                          className={`calendar-month-event ${isTemp ? 'is-temporary' : ''}`}
                          style={{ borderLeftColor: colors[event.kind], background: calendarBlockColor(event.kind, isTemp) }}
                          onClick={() => { if (!isTemp) edit(event); }}
                        >
                          {event.start} {event.title}
                        </button>
                      );
                    })}
                    {items.length > 4 && (
                      <Button size="xs" variant="subtle" onClick={() => { setDate(day); setView('week'); }}>
                        Ещё {items.length - 4}
                      </Button>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* Legend */}
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-[#929292]">
            {['task', 'busy', 'sleep', 'meal', 'break', 'work'].map((kind) => (
              <span key={kind} className="inline-flex items-center gap-1.5 border border-[#242424] bg-[#111111] px-2 py-0.5">
                <span className="h-2.5 w-2.5 inline-block" style={{ background: colors[kind] }} />
                <span>{names[kind]}</span>
              </span>
            ))}
            {features.ameba !== false && showAmeba && (
              <span className="inline-flex items-center gap-1.5 border border-dashed border-[#7F49B4] bg-[#7F49B4]/12 px-2 py-0.5 text-[#DFC4F8]">
                <span>Ameba</span>
              </span>
            )}
          </div>
        </div>

        {/* Selected Day Side Panel */}
        <SlidePanel open={dayPanelOpen} className="border border-[#303030] bg-[#111111] p-3 flex flex-col gap-3">
          <div className="flex items-center justify-between gap-2 border-b border-[#222222] pb-2">
            <div className="text-[13px] font-semibold text-[#EAEAEA]">
              {asDate(selectedDay).toLocaleDateString('ru-RU', { weekday: 'short', day: 'numeric', month: 'long' })}
            </div>
            <Button size="xs" variant="primary" onClick={() => create(selectedDay)}>+ Событие</Button>
          </div>

          <div className="flex flex-col gap-1.5">
            <div className="text-[11px] font-medium uppercase tracking-wider text-[#929292]">События</div>
            {selectedDayEvents.length === 0 ? (
              <div className="text-[12px] text-[#787878]">Нет событий</div>
            ) : (
              selectedDayEvents.map((ev) => (
                <button
                  key={ev.id}
                  type="button"
                  onClick={() => edit(ev)}
                  className="text-left border border-[#262626] border-l-2 bg-[#0D0D0D] hover:bg-[#191919] px-2.5 py-1.5 cursor-pointer"
                  style={{ borderLeftColor: colors[ev.kind] }}
                >
                  <div className="flex items-center justify-between gap-2 text-[11px] text-[#929292] tabular-nums">
                    <span>{ev.start}–{ev.end}</span>
                    <span>{names[ev.kind] ?? ev.kind}</span>
                  </div>
                  <div className="text-[12px] font-medium text-[#EAEAEA] break-words">{ev.title}</div>
                </button>
              ))
            )}
          </div>

          {features.ameba !== false && showAmeba && (
            <div className="border-t border-[#222222] pt-2.5 flex flex-col gap-1.5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] font-medium uppercase tracking-wider text-[#CFA9F5]">Ameba</span>
                <a href="/ameba" className="text-[11px] text-[#B98CE8] hover:underline">План →</a>
              </div>
              {selectedDayAmeba?.tasks?.length ? (
                selectedDayAmeba.tasks.map((t) => (
                  <a
                    key={t.task_id}
                    href={`/?task=${t.task_id}`}
                    className="border border-dashed border-[#7F49B4]/60 bg-[#7F49B4]/10 px-2.5 py-1.5 flex items-center justify-between gap-2 text-[12px] hover:bg-[#7F49B4]/20"
                  >
                    <span className="truncate text-[#EAEAEA]">{t.title}</span>
                    <span className="text-[11px] text-[#CFA9F5] tabular-nums shrink-0">{t.hours_label}</span>
                  </a>
                ))
              ) : (
                <div className="text-[12px] text-[#787878]">Нет задач</div>
              )}
            </div>
          )}
        </SlidePanel>
      </div>

      {/* Straus Slide-Over Drawer */}
      {config && features.straus !== false && (
        <Drawer opened={strausOpen} onClose={() => setStrausOpen(false)} title="Straus · расписание">
          <div className="flex flex-col gap-3">
            <Field label="Поручение">
              <Textarea
                rows={3}
                maxLength={12000}
                placeholder="Например: добавь пары группы из ЛК на две недели"
                value={command}
                disabled={busy}
                onChange={(event) => setCommand(event.currentTarget.value)}
              />
            </Field>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-1.5">
                <Button
                  variant="primary"
                  disabled={!command.trim() || busy || status.isPending || status.isError}
                  onClick={() => {
                    stop.reset();
                    editAgent.mutate();
                  }}
                >
                  Применить
                </Button>
                {status.data?.running && (
                  <Button variant="danger" loading={stop.isPending} onClick={() => stop.mutate()}>
                    Остановить
                  </Button>
                )}
              </div>
              <a href="/profile?tab=access" className="text-[12px] text-[#CFA9F5] hover:underline">
                Кабинеты
              </a>
            </div>

            {editAgent.isSuccess && (
              editAgent.data?.changed ? (
                <Notice tone="success">Календарь обновлён.</Notice>
              ) : (
                <Notice
                  tone={editAgent.data?.status === 'blocked' ? 'warning' : 'info'}
                  title={editAgent.data?.status === 'blocked' ? 'Изменения не внесены' : 'Изменения не нужны'}
                >
                  {editAgent.data?.reason}
                </Notice>
              )
            )}
            {(editAgent.error || (editAgent.isSuccess && editAgent.data?.status === 'blocked')) && (
              <AgentProblemReport agent="Straus" toolName="calendar_edit" />
            )}
            {status.data?.error && !busy && <Notice tone="warning">{status.data.error}</Notice>}
            {(editAgent.error || stop.error) && <Notice tone="error">{(editAgent.error || stop.error).message}</Notice>}

            <AgentProgress providers={['straus']} active={busy} defaultOpen />
          </div>
        </Drawer>
      )}

      {/* Permanent Event Editor Modal */}
      <Modal opened={Boolean(editor)} onClose={() => setEditor(null)} title="Событие календаря" size="md">
        {editor && (
          <div className="flex flex-col gap-3">
            <Field label="Название">
              <Input
                value={editor.title}
                maxLength={150}
                onChange={(event) => setEditor({ ...editor, title: event.currentTarget.value })}
              />
            </Field>
            <Field label="Краткая сводка">
              <Textarea
                rows={2}
                maxLength={500}
                value={editor.summary}
                onChange={(event) => setEditor({ ...editor, summary: event.currentTarget.value })}
              />
            </Field>
            <Field label="Тип">
              <SelectField
                value={editor.kind}
                options={Object.entries(names).filter(([k]) => k !== 'personal').map(([value, label]) => ({ value, label }))}
                onChange={(kind) => {
                  if (kind) setEditor({ ...editor, kind, task_id: kind === 'task' ? editor.task_id : null });
                }}
              />
            </Field>
            {editor.kind === 'task' && (
              <Field label="Задача">
                <Combobox
                  placeholder="Выберите задачу"
                  searchPlaceholder="Поиск задачи…"
                  value={editor.task_id ? String(editor.task_id) : null}
                  options={taskList.map((task) => ({ value: String(task.id), label: task.title }))}
                  onChange={(value) => {
                    const task = taskList.find((item) => item.id === Number(value));
                    setEditor({
                      ...editor,
                      task_id: value ? Number(value) : null,
                      title: task?.title.slice(0, 150) ?? editor.title,
                      summary: (task?.description ?? '').slice(0, 500),
                    });
                  }}
                />
              </Field>
            )}
            {editor.task_id && (
              <a href={`/?task=${editor.task_id}`} className="text-[12px] text-[#CFA9F5] hover:underline w-fit">
                Открыть полную задачу →
              </a>
            )}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              <Field label="Дата начала">
                <Input type="date" value={editor.date} onChange={(event) => setEditor({ ...editor, date: event.currentTarget.value })} />
              </Field>
              <Field label="Начало">
                <Input placeholder="09:00" value={editor.start} onChange={(event) => setEditor({ ...editor, start: event.currentTarget.value })} />
              </Field>
              <Field label="Конец">
                <Input placeholder="10:00" value={editor.end} onChange={(event) => setEditor({ ...editor, end: event.currentTarget.value })} />
              </Field>
            </div>
            <Field label="Повторение">
              <SelectField
                value={editor.repeat_unit}
                options={units}
                onChange={(repeat_unit) => {
                  if (repeat_unit) {
                    setEditor({ ...editor, repeat_unit, week_pattern: repeat_unit === 'week' ? (editor.week_pattern ?? 'all') : 'all' });
                  }
                }}
              />
            </Field>
            {editor.repeat_unit !== 'none' && (
              <div className="flex flex-col gap-2">
                <div className="grid grid-cols-2 gap-2">
                  <Field label="Каждые N дней / недель / мес.">
                    <Input
                      type="number"
                      min={1}
                      max={366}
                      value={editor.repeat_interval}
                      onChange={(event) => setEditor({ ...editor, repeat_interval: Math.max(1, Number(event.currentTarget.value) || 1) })}
                    />
                  </Field>
                  <Field label="Количество повторений">
                    <Input
                      type="number"
                      min={1}
                      max={10000}
                      placeholder="Без ограничения"
                      value={editor.repeat_count ?? ''}
                      onChange={(event) => setEditor({ ...editor, repeat_count: event.currentTarget.value === '' ? null : Math.max(1, Number(event.currentTarget.value)) })}
                    />
                  </Field>
                </div>
                <p className="text-[11px] text-[#7D7D7D] m-0">Меняется вся серия.</p>
              </div>
            )}
            {editor.repeat_unit === 'week' && (
              <Field label="Учебные недели">
                <SelectField
                  value={editor.week_pattern ?? 'all'}
                  options={[
                    { value: 'all', label: 'Все недели' },
                    { value: 'even', label: 'Чётные' },
                    { value: 'odd', label: 'Нечётные' },
                  ]}
                  onChange={(week_pattern) => { if (week_pattern) setEditor({ ...editor, week_pattern }); }}
                />
              </Field>
            )}
            {editor.repeat_unit === 'week' && editor.week_pattern && editor.week_pattern !== 'all' && (
              <Field label="Понедельник первой нечётной недели" hint="Не задан — используется ISO-чётность.">
                <Input
                  type="date"
                  value={editor.parity_anchor ?? ''}
                  onChange={(event) => setEditor({ ...editor, parity_anchor: event.currentTarget.value || null })}
                />
              </Field>
            )}
            {(error || save.error) && <Notice tone="error">{error || save.error.message}</Notice>}
            <div className="flex items-center justify-between gap-2 pt-2 border-t border-[#222222]">
              <div>
                {config?.events.some((event) => event.id === editor.id) && (
                  <Button
                    variant="danger-ghost"
                    disabled={save.isPending}
                    onClick={() => save.mutate({ ...config, events: config.events.filter((event) => event.id !== editor.id) })}
                  >
                    Удалить {editor.repeat_unit !== 'none' ? 'серию' : 'событие'}
                  </Button>
                )}
              </div>
              <div className="flex items-center gap-2">
                <Button variant="default" onClick={() => setEditor(null)}>Отмена</Button>
                <Button variant="primary" loading={save.isPending} onClick={saveEvent}>Сохранить</Button>
              </div>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
