"""Вопрос владельцу: агент ждёт ответ, не вызывая модель в цикле."""
import asyncio
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field

from common.identity import user_id


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    options: list[str] = Field(max_length=6)


class Answer(BaseModel):
    text: str = Field(min_length=1, max_length=12000)


QUESTION_TOOL = {
    'type': 'function', 'name': 'ask_user', 'strict': True,
    'description': 'Задать владельцу существенный вопрос и дождаться его ответа. options — короткие варианты или пустой список; свободный ответ доступен всегда. Не проси пароли и секреты.',
    'parameters': {'type': 'object', 'properties': {
        'question': {'type': 'string', 'minLength': 1, 'maxLength': 2000},
        'options': {'type': 'array', 'items': {'type': 'string', 'minLength': 1, 'maxLength': 250}, 'maxItems': 6},
    }, 'required': ['question', 'options'], 'additionalProperties': False},
}
QUESTION_INSTRUCTION = (
    'Если не хватает существенного факта, который знает владелец, используй ask_user. '
    'Это настоящий вопрос с ожиданием ответа: после ответа продолжай текущее поручение. '
    'Не заменяй вопрос заметкой agent_note и не пиши «жду ответа», продолжая гадать. '
    'Спрашивай только о том, что влияет на результат и не найдено в памяти/контексте/источниках. '
    'Не требуй уточнять очевидное; количество действительно необходимых вопросов не ограничено. '
    'При неоднозначном жёстком дедлайне уточни, принимают ли работу после срока. '
    'При ответе «не знаю» объясни допущение или ограничение, не повторяй один вопрос по кругу. '
    'Новые подтверждённые устойчивые предпочтения из ответа можно сохранить в общую память. '
    'Для CAPTCHA/2FA используй browser_wait_for_user; не проси передавать пароли и коды в чат. '
)

pending = {}


async def ask_user(arguments, progress):
    body = Question.model_validate_json(arguments)
    if not body.question.strip():
        raise ValueError('Укажи понятный вопрос владельцу')
    question_id = uuid4().hex
    key = (user_id(), question_id)
    future = asyncio.get_running_loop().create_future()
    pending[key] = future
    previous_stage = progress.record.get('stage')
    progress.add('Вопрос: ' + body.question.strip())
    progress.update(stage='waiting_user', model_waiting=False,
                    pending_question={'id': question_id, 'question': body.question.strip(),
                                      'options': [value.strip() for value in body.options if value.strip()]})
    try:
        answer = await future
        progress.add('Ответ владельца получен. Продолжает работу')
        return {'answer': answer}
    finally:
        pending.pop(key, None)
        progress.update(pending_question=None, stage=previous_stage)


def answer_question(question_id, text):
    future = pending.get((user_id(), question_id))
    if future is None or future.done():
        raise HTTPException(409, 'Этот вопрос уже закрыт. Обновите журнал агента')
    if not text.strip():
        raise HTTPException(422, 'Введите ответ')
    future.set_result(text.strip())
    return {'status': 'answered'}
