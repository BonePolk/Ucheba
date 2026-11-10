"""Последний журнал агента: действия и публичные пояснения, без секретов."""
from collections import OrderedDict
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from common.identity import user_id

records = OrderedDict()
records_lock = Lock()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class Progress:
    def __init__(self, agent, task_id=0, namespace='agent'):
        self.key = (user_id(), task_id, namespace)
        self.record = {'id': uuid4().hex, 'agent': agent, 'task_id': task_id,
                       'status': 'running', 'started_at': timestamp(), 'events': []}
        self.sequence = 0
        self.summary_key = None

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
        if self.record['agent'] not in ('Lucheba', 'Jeba', 'Ameba'):
            self.add('Начало работы')
        return self

    def add(self, message):
        with records_lock:
            self.sequence += 1
            self.record['events'].append({'id': self.sequence, 'at': timestamp(), 'message': message})
            self.record['events'] = self.record['events'][-100:]

    def update(self, **values):
        with records_lock:
            self.record.update(values)

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

    def __exit__(self, kind, error, traceback):
        status = 'completed' if kind is None else 'failed' if isinstance(error, Exception) else 'interrupted'
        message = {'completed': 'Работа завершена', 'failed': 'Операция завершилась ошибкой', 'interrupted': 'Работа прервана'}[status]
        if self.record['agent'] not in ('Lucheba', 'Jeba', 'Ameba'):
            self.add(message)
        with records_lock:
            self.record['status'] = status
            self.record['finished_at'] = timestamp()


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
