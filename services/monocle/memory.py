"""Память агента: один закрытый префикс Silo на пользователя."""
import io
import json
import os
import re

from fastapi import HTTPException
from minio import Minio
from minio.error import S3Error
from urllib3 import PoolManager
from urllib3.util import Timeout

from common.identity import user_id


class Memory:
    def __init__(self):
        self.prefix = f'users/{user_id()}/context/'
        self.bucket = 'context-memory'
        access = os.getenv('SILO_ACCESS_KEY', '')
        secret = os.getenv('SILO_SECRET_KEY', '')
        if not access or not secret:
            raise HTTPException(503, 'Настройте доступ context к Silo')
        self.client = Minio(
            'silo:9000', access_key=access, secret_key=secret, secure=False,
            http_client=PoolManager(timeout=Timeout(connect=3, read=5), retries=False),
        )
        try:
            self.client.make_bucket(self.bucket)
        except S3Error as exc:
            if exc.code not in ('BucketAlreadyOwnedByYou', 'BucketAlreadyExists'):
                raise

    def list_files(self):
        files = []
        for item in self.client.list_objects(self.bucket, prefix=self.prefix, recursive=True):
            name = item.object_name[len(self.prefix):]
            if not re.fullmatch(r'task-[1-9][0-9]*\.json', name):
                continue
            files.append(name)
            if len(files) == 100:
                break
        return files

    def key(self, filename):
        # Модель выбирает только имя файла, но никогда пользователя или путь.
        if not isinstance(filename, str) or not re.fullmatch(r'task-[1-9][0-9]*\.json', filename):
            raise ValueError('Недопустимое имя файла памяти')
        return self.prefix + filename

    def read(self, filename):
        return self.read_bytes(self.key(filename)[len(self.prefix):], 32000).decode('utf-8', errors='replace')

    def read_bytes(self, key, limit=15 * 1024 * 1024):
        response = self.client.get_object(self.bucket, self.prefix + key)
        try:
            return response.read(limit)
        finally:
            response.close()
            response.release_conn()

    def put_bytes(self, key, content, mime):
        self.client.put_object(self.bucket, self.prefix + key, io.BytesIO(content), len(content), content_type=mime)

    def save(self, task_id, data):
        content = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.client.put_object(self.bucket, self.key(f'task-{task_id}.json'),
                               io.BytesIO(content), len(content), content_type='application/json')
