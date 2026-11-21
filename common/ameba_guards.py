"""Независимые программные проверки подтверждённого прогресса и текстовых ограничений Ameba."""
from __future__ import annotations

import re

_WORD_NUMBERS = (
    ('сорок пять', 45),
    ('сорока пяти', 45),
    ('полтора', 90),
    ('полторы', 90),
    ('двадцать', 20),
    ('двадцати', 20),
    ('тридцать', 30),
    ('тридцати', 30),
    ('пятнадцать', 15),
    ('пятнадцати', 15),
    ('пятьдесят', 50),
    ('пятидесяти', 50),
    ('четырех', 4),
    ('четыре', 4),
    ('четырьмя', 4),
    ('десять', 10),
    ('десяти', 10),
    ('девять', 9),
    ('девяти', 9),
    ('восемь', 8),
    ('восьми', 8),
    ('шесть', 6),
    ('шести', 6),
    ('семь', 7),
    ('семи', 7),
    ('сорок', 40),
    ('сорока', 40),
    ('одного', 1),
    ('одной', 1),
    ('один', 1),
    ('одну', 1),
    ('одна', 1),
    ('двух', 2),
    ('двумя', 2),
    ('два', 2),
    ('две', 2),
    ('трех', 3),
    ('тремя', 3),
    ('три', 3),
    ('пять', 5),
    ('пяти', 5),
)

_PAST_DONE_RE = re.compile(
    r'\b(?:'
    r'уже|сделал[аои]?|сделан[аоы]?|выполнил[аои]?|выполнен[аоы]?|'
    r'перевез(?:л[аои])?|позанимал(?:ся|ась|ись)|поработал[аои]?|отработал[аои]?|'
    r'потратил[аои]?|затратил[аои]?|ушло|ушел|ушла|прошел|прошла|прошли|'
    r'написал[аои]?|решил[аои]?|доделал[аои]?|завершил[аои]?|закончил[аои]?|'
    r'прочитал[аои]?|изучил[аои]?|собрал[аои]?|подготовил[аои]?|'
    r'выполнено|готово|done|completed|finished|worked|spent'
    r')\b'
)

_NEGATED_DONE_RE = re.compile(
    r'\b(?:не|пока не|еще не)\s+(?:'
    r'сделал[аои]?|сделано|выполнил[аои]?|выполнено|перевез(?:л[аои])?|'
    r'позанимал(?:ся|ась|ись)|поработал[аои]?|отработал[аои]?|потратил[аои]?|'
    r'затратил[аои]?|написал[аои]?|решил[аои]?|доделал[аои]?|завершил[аои]?|'
    r'закончил[аои]?|прочитал[аои]?|изучил[аои]?|подготовил[аои]?|готово'
    r')\b'
)

_FUTURE_OR_PLAN_RE = re.compile(
    r'\b(?:'
    r'буду|будем|будет|будешь|будете|будут|планирую|собираюсь|хочу|нужно|надо|'
    r'завтра|послезавтра|перенеси|перенести|сдвинь|сдвинуть|поставь|поставить|'
    r'запланируй|запланировать|освободи|освободить|оставь|оставить|сделай|сделать|'
    r'убери|убрать|сделаю|сделаем|сделает|выполню|закончу|доделаю|'
    r'will|going to|tomorrow|move|reschedule|schedule|postpone|shift|keep free|leave'
    r')\b'
)

_PROGRESS_QUESTION_RE = re.compile(
    r'\b(?:сколько|минут|часов|сделал|выполн|потрат|прогресс|осталось|готово|spent|done|minutes|hours)\b'
)

_CUMULATIVE_SET_RE = re.compile(
    r'\b(?:'
    r'всего|итого|в сумме|суммарно|в общей сложности|установи|поставь прогресс|'
    r'исправь|ошибся|ошиблась|на самом деле|сбрось|обнули|вместо|total|set|reset|overall|in total'
    r')\b'
)

_CORRECTION_SET_RE = re.compile(
    r'\b(?:исправь|ошибся|ошиблась|на самом деле|сбрось|обнули|вместо|всего только|только|reset|actually)\b'
)

_APPROX_RE = re.compile(r'\b(?:примерно|около|приблизительно|порядка|где-то|roughly|about|approx)\b')

_STOP_WORDS = frozenset({
    'задача', 'задачи', 'задачу', 'задаче', 'задач', 'работа', 'работы', 'работу', 'работе',
    'сделать', 'сделал', 'сделала', 'сделали', 'делаю', 'делать', 'план', 'плана', 'плану',
    'время', 'времени', 'минут', 'минуты', 'минуту', 'часов', 'часа', 'час', 'осталось',
    'половину', 'половина', 'треть', 'четверть', 'примерно', 'около', 'всего', 'итого',
    'сегодня', 'завтра', 'вчера', 'послезавтра', 'перенеси', 'оставь', 'освободи', 'уже',
    'еще', 'или', 'для', 'под', 'над', 'при', 'про', 'без', 'после', 'перед', 'через',
    'task', 'work', 'plan', 'time', 'minutes', 'hours', 'already', 'half',
})

_TIME_SPAN_STRIP_RE = re.compile(
    r'\b\d+(?:[.,]\d+)?\s*(?:ч(?:ас\w*)?|h|мин\w*|м|m|%)\b',
    re.IGNORECASE,
)


def norm_text(text: str) -> str:
    return re.sub(r'\s+', ' ', str(text or '').strip().casefold().replace('ё', 'е'))


def extract_explicit_minutes(text: str) -> list[int]:
    """Извлекает явно названные длительности в минутах из нормализованного текста."""
    work = f' {norm_text(text)} '
    found: list[int] = []

    def consume(pattern: str, fn) -> None:
        nonlocal work

        def _repl(match: re.Match) -> str:
            val = fn(match)
            if val is not None and val >= 0:
                found.append(int(val))
            return ' '

        work = re.sub(pattern, _repl, work)

    consume(r'\bполтор[аы]\s*(?:час\w*|ч)\b', lambda _m: 90)
    consume(r'\bпол(?:\s+|-)час\w*\b|\bпол(?:\s+|-)ч\b', lambda _m: 30)
    consume(
        r'\b(\d+(?:[.,]\d+)?)\s*(?:ч(?:ас\w*)?|h)\s*(?:и\s*)?(\d+)\s*(?:мин\w*|м|m)\b',
        lambda m: round(float(m.group(1).replace(',', '.')) * 60) + int(m.group(2)),
    )

    for word, num in _WORD_NUMBERS:
        esc = re.escape(word)
        consume(
            rf'\b{esc}\s+час\w*\s+(?:и\s*)?(\d+)\s*(?:мин\w*|м)\b',
            lambda m, n=num: n * 60 + int(m.group(1)),
        )
        if num in (90,):
            continue
        consume(
            rf'\b{esc}\s+час\w*(?:\s+с\s+половиной)?\b',
            lambda m, n=num: n * 60 + (30 if 'с половиной' in m.group(0) else 0),
        )
        consume(rf'\b{esc}\s+мин\w*\b', lambda _m, n=num: n)

    consume(
        r'\b(\d+(?:[.,]\d+)?)\s*(?:ч(?:ас\w*)?|h)(?:\s+с\s+половиной)?\b',
        lambda m: round(float(m.group(1).replace(',', '.')) * 60) + (30 if 'с половиной' in m.group(0) else 0),
    )
    consume(r'\b(\d+)\s*(?:мин\w*|м|m)\b', lambda m: int(m.group(1)))
    consume(r'\bчас\b', lambda _m: 60)

    unique: list[int] = []
    for val in found:
        if val not in unique:
            unique.append(val)
    return unique


def extract_fraction(text: str) -> float | None:
    norm = norm_text(text)
    if re.search(r'\b(?:половин\w*|пол\s*-?\s*(?:задач\w*|работ\w*|лаб\w*)|1\s*/\s*2|half)\b', norm):
        return 0.5
    if re.search(r'\b(?:две трети|2\s*/\s*3)\b', norm):
        return 2.0 / 3.0
    if re.search(r'\b(?:треть|трети|1\s*/\s*3|third)\b', norm):
        return 1.0 / 3.0
    if re.search(r'\b(?:три четверти|3\s*/\s*4)\b', norm):
        return 0.75
    if re.search(r'\b(?:четверть|четверти|1\s*/\s*4|quarter)\b', norm):
        return 0.25
    pct = re.search(r'\b(\d{1,2})\s*%', norm)
    if pct:
        val = int(pct.group(1))
        if 1 <= val <= 99:
            return val / 100.0
    return None


def task_matches_text(task: dict, text_norm: str) -> int:
    """Оценивает степень упоминания конкретной задачи в нормализованном тексте."""
    tid = task.get('id')
    score = 0
    if tid is not None and re.search(rf'(?:#|№|\bзадач[ауеи]?\s*№?\s*){tid}\b', text_norm):
        score += 4

    stripped_time = _TIME_SPAN_STRIP_RE.sub(' ', text_norm)
    parts = [task.get('title') or '', task.get('subject') or '', task.get('category') or '']
    seen_tokens: set[str] = set()
    for part in parts:
        for token in re.findall(r'[a-zа-я0-9]+', norm_text(part)):
            if token in _STOP_WORDS or token in seen_tokens:
                continue
            seen_tokens.add(token)
            if token.isdigit():
                if re.search(rf'\b{re.escape(token)}\b', stripped_time):
                    score += 2
            elif len(token) >= 4:
                stem = token[:5] if len(token) >= 6 else token[:4]
                if re.search(rf'\b{re.escape(stem)}[a-zа-я]*\b', text_norm):
                    score += 1
            elif len(token) >= 2 and re.search(rf'\b{re.escape(token)}\b', text_norm):
                score += 1
    return score


def _split_clauses(text_norm: str) -> list[str]:
    parts = re.split(
        r'[,;.!?\n]+|\s+[—-]\s+|\s+(?:но|а|зато)\s+|\s+и\s+(?=[^,;.!?\n]*(?:завтра|послезавтра|буду|будем|планирую|перенес|оставь|освобод|поставь|запланир|сделаю))',
        text_norm,
    )
    return [p.strip() for p in parts if p and p.strip()]


def _has_uncertain_task_estimate(task: dict) -> bool:
    raw = norm_text(task.get('time_estimate') or '')
    if 'недостаточно данных' in raw:
        subs = task.get('subtasks') or []
        return not any(isinstance(s, dict) and s.get('estimated_minutes') for s in subs)
    return False


def verify_progress_update(
    update: dict,
    task: dict,
    *,
    user_feedback: str = '',
    user_answers: list[dict] | None = None,
    all_tasks: list[dict] | None = None,
    total_minutes_min: int | None = None,
    total_minutes_max: int | None = None,
) -> tuple[bool, str, str]:
    """Проверяет, подтверждено ли обновление completed_work_minutes явным сообщением владельца."""
    feedback_norm = norm_text(user_feedback)
    answer_pairs = [
        (norm_text(item.get('question') or ''), norm_text(item.get('answer') or ''))
        for item in (user_answers or [])
        if isinstance(item, dict) and (item.get('answer') or '').strip()
    ]
    if not feedback_norm and not answer_pairs:
        return False, 'progress_update_without_feedback', 'progress_updates разрешены только при явном сообщении владельца.'

    quote_raw = str(update.get('evidence_quote') or '').strip()
    quote_norm = norm_text(quote_raw)
    if len(quote_norm) < 2:
        return False, 'unverified_progress_update', 'Для изменения прогресса нужна дословная цитата evidence_quote из слов владельца.'

    matched_question = ''
    source_text = ''
    from_answer = False
    if feedback_norm and quote_norm in feedback_norm:
        source_text = feedback_norm
    else:
        for q_norm, a_norm in answer_pairs:
            if quote_norm in a_norm:
                matched_question = q_norm
                source_text = f'{q_norm} {a_norm}'.strip()
                from_answer = True
                break
    if not source_text:
        return False, 'unverified_progress_update', 'Цитата evidence_quote не найдена в словах владельца.'

    op = update.get('operation')
    try:
        mins = int(update.get('minutes'))
    except (TypeError, ValueError):
        return False, 'invalid_progress_update', 'Некорректное значение минут в progress_updates.'
    if op == 'add' and mins <= 0:
        return False, 'invalid_progress_update', 'Для operation="add" количество минут должно быть больше 0.'
    if op == 'set' and mins < 0:
        return False, 'invalid_progress_update', 'Для operation="set" количество минут должно быть не меньше 0.'

    current_done = max(0, int(task.get('completed_work_minutes') or 0))
    clauses = _split_clauses(quote_norm)
    confirmed_clauses: list[str] = []
    question_is_progress = from_answer and bool(_PROGRESS_QUESTION_RE.search(matched_question))
    for clause in clauses:
        if _FUTURE_OR_PLAN_RE.search(clause) or _NEGATED_DONE_RE.search(clause):
            continue
        if _PAST_DONE_RE.search(clause):
            confirmed_clauses.append(clause)
        elif question_is_progress and (extract_explicit_minutes(clause) or extract_fraction(clause) is not None):
            confirmed_clauses.append(clause)

    if not confirmed_clauses:
        return (
            False,
            'unverified_progress_update',
            f'Цитата «{quote_raw}» не подтверждает уже выполненную работу (запрос на перенос/план на будущее не меняет прогресс).',
        )

    # Проверяем количественное совпадение с подтверждённой подфразой.
    matched_amount = False
    had_fraction_only = False
    for clause in confirmed_clauses:
        explicit = extract_explicit_minutes(clause)
        if explicit:
            tol = 5 if _APPROX_RE.search(clause) else 0
            if any(abs(val - mins) <= tol for val in explicit):
                matched_amount = True
                break
            continue
        frac = extract_fraction(clause)
        if frac is not None:
            had_fraction_only = True
            if _has_uncertain_task_estimate(task):
                return (
                    False,
                    'ambiguous_progress_update',
                    f'Доля выполнения («{quote_raw}») неоднозначна при неизвестной длительности задачи — уточни минуты через ask_user.',
                )
            if total_minutes_max is None or total_minutes_max <= 0:
                return (
                    False,
                    'ambiguous_progress_update',
                    'Нельзя перевести долю выполнения в минуты без надёжной оценки общей длительности.',
                )
            t_min = max(0, int(total_minutes_min if total_minutes_min is not None else total_minutes_max))
            t_max = max(t_min, int(total_minutes_max))
            if t_max - t_min > max(30, round(t_max * 0.35)):
                return (
                    False,
                    'ambiguous_progress_update',
                    f'Оценка длительности слишком широка ({t_min}–{t_max} мин) для интерпретации доли «{quote_raw}» — уточни через ask_user.',
                )
            low_share = round(t_min * frac)
            high_share = round(t_max * frac)
            tol = max(10, round(t_max * 0.1))
            if op == 'set':
                if low_share - tol <= mins <= high_share + tol:
                    matched_amount = True
                    break
            else:
                rem_low = max(0, t_min - current_done)
                rem_high = max(rem_low, t_max - current_done)
                if (
                    (current_done == 0 and low_share - tol <= mins <= high_share + tol)
                    or (current_done > 0 and (
                        max(1, low_share - current_done) - tol <= mins <= max(1, high_share - current_done) + tol
                        or round(rem_low * frac) - tol <= mins <= round(rem_high * frac) + tol
                    ))
                ):
                    matched_amount = True
                    break

    if not matched_amount:
        if had_fraction_only:
            return False, 'ambiguous_progress_update', f'Указанные минуты ({mins}) не согласуются с долей «{quote_raw}».'
        return False, 'unverified_progress_update', f'Указанные минуты ({mins}) не подтверждены цитатой «{quote_raw}».'

    # Строгая защита operation="set" от случайной перезаписи.
    if op == 'set':
        set_context = f'{matched_question} {quote_norm}'.strip() if from_answer else quote_norm
        if not _CUMULATIVE_SET_RE.search(set_context):
            return (
                False,
                'invalid_progress_set',
                'operation="set" допустим только при явном указании итогового суммарного времени или исправления прогресса.',
            )
        if current_done > 0 and mins < current_done and not _CORRECTION_SET_RE.search(set_context):
            return (
                False,
                'invalid_progress_set',
                f'Нельзя уменьшить уже подтверждённый прогресс ({current_done} -> {mins} мин) без явной просьбы исправить/сбросить.',
            )

    # Однозначность определения задачи, если активных задач больше одной.
    if all_tasks and len(all_tasks) > 1:
        tid = task.get('id')
        target_score = task_matches_text(task, source_text)
        other_scores = [task_matches_text(other, source_text) for other in all_tasks if other.get('id') != tid]
        max_other = max(other_scores, default=0)
        if target_score <= 0 or target_score <= max_other:
            return (
                False,
                'ambiguous_progress_update',
                f'По словам владельца не определена однозначно задача #{tid} для обновления прогресса — уточни через ask_user.',
            )

    return True, '', ''
