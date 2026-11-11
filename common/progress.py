"""Последний журнал агента: действия и публичные пояснения, без секретов."""
import asyncio
import json
from collections import OrderedDict
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from common.identity import user_id

records = OrderedDict()
records_lock = Lock()
subscribers = {}


NOTE_TOOL = {
    'type': 'function', 'name': 'agent_note', 'strict': True,
    'description': 'По желанию сообщить владельцу короткое публичное пояснение: что сейчас проверяешь или собираешься сделать. Это не скрытые рассуждения.',
    'parameters': {'type': 'object', 'properties': {'text': {'type': 'string'}},
                   'required': ['text'], 'additionalProperties': False},
}
NOTE_INSTRUCTION = 'По желанию используй agent_note для короткого публичного сообщения о текущем шаге. Не раскрывай скрытые рассуждения. Это необязательно и не заменяет работу инструментами. '


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def notify(owner):
    # Некоторые действия агента выполняются в потоке, поэтому будим asyncio безопасно.
    with records_lock:
        listeners = list(subscribers.get(owner, ()))
    for loop, queue in listeners:
        def wake(queue=queue):
            if queue.empty():
                queue.put_nowait(True)
        loop.call_soon_threadsafe(wake)


async def stream_progress(task_id=0, namespace='agent', work=False):
    owner = user_id()
    queue = asyncio.Queue(maxsize=1)
    listener = (asyncio.get_running_loop(), queue)
    with records_lock:
        subscribers.setdefault(owner, set()).add(listener)
    try:
        while True:
            record = get_work_progress() if work else get_progress(task_id, namespace)
            yield 'data: ' + json.dumps(record, ensure_ascii=False) + '\n\n'
            try:
                await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                yield ': keepalive\n\n'
    finally:
        with records_lock:
            subscribers[owner].discard(listener)
            if not subscribers[owner]:
                del subscribers[owner]


class Progress:
    def __init__(self, agent, task_id=0, namespace='agent'):
        self.key = (user_id(), task_id, namespace)
        self.record = {'id': uuid4().hex, 'agent': agent, 'task_id': task_id,
                       'status': 'running', 'started_at': timestamp(), 'events': []}
        self.sequence = 0
        self.summary_key = None
        self.usage_totals = {'input_tokens': None, 'output_tokens': None}
        self.billed_usage = set()
        self.response_number = 0

    def __enter__(self):
        with records_lock:
            records[self.key] = self.record
            records.move_to_end(self.key)
            # Храним ограниченное число завершённых журналов в памяти сервиса.
            for key in list(records):
                if len(records) <= 128:
                    break
                if records[key]['status'] != 'running':
                    del records[key]
        notify(self.key[0])
        if self.record['agent'] not in ('Lucheba', 'Jeba', 'Ameba'):
            self.add('Начало работы')
        return self

    def add(self, message):
        with records_lock:
            self.sequence += 1
            self.record['events'].append({'id': self.sequence, 'at': timestamp(), 'message': message})
            self.record['events'] = self.record['events'][-100:]
            self.record['last_activity_at'] = timestamp()
        notify(self.key[0])

    def update(self, **values):
        with records_lock:
            self.record.update(values)
        notify(self.key[0])

    def begin_response(self):
        self.response_number += 1
        self.reasoning_key = None
        self.update(model_waiting=True, current_usage_known=False,
                    reasoning_text='', response_event_start=self.sequence, last_activity_at=timestamp())

    def note(self, arguments):
        text = json.loads(arguments).get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            return {'error': 'Нужно краткое текстовое пояснение до 500 символов'}
        self.add(text.strip())
        return {'ok': True}

    def finish_response(self, response):
        usage = getattr(response, 'usage', None)
        if usage is not None:
            for name in self.usage_totals:
                value = getattr(usage, name, None)
                key = (self.response_number, name)
                if type(value) is int and key not in self.billed_usage:
                    self.billed_usage.add(key)
                    previous = self.usage_totals[name]
                    self.usage_totals[name] = value if previous is None else previous + value
            if any(type(getattr(usage, name, None)) is int for name in self.usage_totals):
                self.update(token_usage=dict(self.usage_totals), current_usage_known=True)
        self.update(model_waiting=False)

    def model_event(self, event):
        self.update(last_activity_at=timestamp())
        if event.type == 'response.reasoning_summary_text.delta':
            key = (event.item_id, event.summary_index)
            with records_lock:
                text = self.record.get('reasoning_text', '')
                if self.reasoning_key != key and text:
                    text += '\n\n'
                self.record['reasoning_text'] = (text + event.delta)[-24000:]
                self.reasoning_key = key
            self.summary(event.delta, key)
        elif event.type in ('response.completed', 'response.incomplete'):
            self.finish_response(event.response)

    def summary(self, text, key):
        # Склеиваем потоковые части одного публичного пояснения модели.
        with records_lock:
            events = self.record['events']
            if self.summary_key == key and events and events[-1].get('kind') == 'summary':
                events[-1] = dict(events[-1], message=events[-1]['message'] + text)
            else:
                self.sequence += 1
                events.append({'id': self.sequence, 'at': timestamp(), 'message': text, 'kind': 'summary'})
                self.record['events'] = events[-100:]
            self.summary_key = key
            self.record['last_activity_at'] = timestamp()
        notify(self.key[0])

    def __exit__(self, kind, error, traceback):
        status = self.record.get('outcome', 'completed') if kind is None else 'failed' if isinstance(error, Exception) else 'interrupted'
        message = {'completed': 'Работа завершена', 'failed': 'Операция завершилась ошибкой', 'interrupted': 'Работа прервана', 'blocked': 'Нужны данные или доступ', 'unchanged': 'Календарь уже соответствует поручению'}[status]
        if self.record['agent'] not in ('Lucheba', 'Jeba', 'Ameba'):
            self.add(message)
        with records_lock:
            self.record['model_waiting'] = False
            self.record['status'] = status
            self.record['finished_at'] = timestamp()
        notify(self.key[0])


def get_progress(task_id=0, namespace='agent'):
    with records_lock:
        record = records.get((user_id(), task_id, namespace))
        return dict(record, events=list(record['events'])) if record else None


def get_work_progress():
    owner = user_id()
    with records_lock:
        runs = [record for (account, task, namespace), record in records.items()
                if account == owner and namespace == 'agent']
        if not runs:
            return None
        latest = max(runs, key=lambda run: (run['status'] == 'running', run['started_at']))
        return dict(latest, events=[dict(event, task_id=latest['task_id']) for event in latest['events']])
