"""Чтение значений XLSX без запуска Excel и формул."""
import csv
import io
import math
from pathlib import Path
from zipfile import ZipFile
import re
from xml.etree import ElementTree

def spreadsheet_rows(zipped):
    # XLSX хранит часть строк отдельно; ячейка с type=s содержит индекс строки.
    ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    strings = []
    if 'xl/sharedStrings.xml' in zipped.namelist():
        shared = ElementTree.fromstring(zipped.read('xl/sharedStrings.xml'))
        strings = [''.join(node.itertext()) for node in shared.findall('x:si', ns)]
    sheet_names = {}
    if 'xl/workbook.xml' in zipped.namelist() and 'xl/_rels/workbook.xml.rels' in zipped.namelist():
        relations = ElementTree.fromstring(zipped.read('xl/_rels/workbook.xml.rels'))
        targets = {node.get('Id'): node.get('Target', '') for node in relations}
        workbook = ElementTree.fromstring(zipped.read('xl/workbook.xml'))
        for sheet in workbook.findall('x:sheets/x:sheet', ns):
            key = sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            target = targets.get(key, '')
            path = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            sheet_names[path] = sheet.get('name', path)
    for path in sorted(zipped.namelist()):
        if not re.fullmatch(r'xl/worksheets/[^/]+\.xml', path):
            continue
        sheet_name = sheet_names.get(path, path)
        sheet = ElementTree.fromstring(zipped.read(path))
        for row in sheet.findall('x:sheetData/x:row', ns):
            cells = {}
            for cell in row.findall('x:c', ns):
                value = cell.findtext('x:v', default='', namespaces=ns)
                if cell.get('t') == 's':
                    index = int(value) if value else -1
                    value = strings[index] if 0 <= index < len(strings) else ''
                elif cell.get('t') == 'inlineStr':
                    inline = cell.find('x:is', ns)
                    value = ''.join(inline.itertext()) if inline is not None else ''
                elif cell.get('t') == 'b':
                    value = 'TRUE' if value == '1' else 'FALSE'
                formula = cell.findtext('x:f', namespaces=ns)
                if formula:
                    value = value + ' [формула: ' + formula + '; сохранённое значение]'
                column = re.sub(r'[^A-Z]', '', cell.get('r', ''))
                cells[column] = value
            yield sheet_name, int(row.get('r', '0')), cells


def spreadsheet_text(zipped):
    lines = []
    length = 0
    previous = None
    for sheet, number, cells in spreadsheet_rows(zipped):
        if sheet != previous:
            lines.append('Лист: ' + sheet)
            previous = sheet
        line = ' | '.join(f'{column}{number}: {value}' for column, value in cells.items())
        lines.append(line)
        length += len(line) + 1
        if length > 40000:
            break
    return '\n'.join(lines)[:40000]


def column_name(index):
    name = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def table_rows(name, content):
    if len(content) > 10 * 1024 * 1024:
        raise ValueError('Таблица больше 10 МБ')
    suffix = Path(name).suffix.lower()
    if suffix == '.xlsx':
        with ZipFile(io.BytesIO(content)) as zipped:
            if len(zipped.infolist()) > 2000 or sum(item.file_size for item in zipped.infolist()) > 20 * 1024 * 1024:
                raise ValueError('Слишком большой распакованный XLSX')
            yield from spreadsheet_rows(zipped)
    elif suffix in ('.csv', '.tsv'):
        try:
            text = content.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = content.decode('cp1251')
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel_tab if suffix == '.tsv' else csv.excel
        for number, row in enumerate(csv.reader(io.StringIO(text), dialect), 1):
            yield 'CSV', number, {column_name(index): value for index, value in enumerate(row, 1)}
    else:
        raise ValueError('Поиск поддерживает XLSX, CSV и TSV. Для XLS нужен экспорт в XLSX/CSV')


def matches(value, operator, expected):
    actual, expected = value.casefold().strip(), expected.casefold().strip()
    if operator == 'contains':
        return expected in actual
    if operator == 'eq':
        return actual == expected
    if operator == 'ne':
        return actual != expected
    if operator == 'empty':
        return not actual
    if operator == 'not_empty':
        return bool(actual)
    if operator not in ('gt', 'gte', 'lt', 'lte'):
        raise ValueError('Неизвестный оператор фильтра')
    try:
        left = float(actual.split(' [формула:', 1)[0].replace(' ', '').replace(',', '.'))
        right = float(expected.replace(' ', '').replace(',', '.'))
    except ValueError:
        return False
    return {'gt': left > right, 'gte': left >= right, 'lt': left < right, 'lte': left <= right}[operator]


def search_table(name, content, args):
    filters, columns = args.get('filters', []), args.get('columns', [])
    offset, limit = args.get('offset', 0), args.get('limit', 30)
    if not 0 <= offset <= 1000000 or not 1 <= limit <= 100 or len(filters) > 20 or len(columns) > 50:
        raise ValueError('Допустимы limit=1..100, offset=0..1000000, до 20 фильтров и 50 колонок')
    for column in columns + [item['column'] for item in filters]:
        if not re.fullmatch('[A-Z]{1,3}', column):
            raise ValueError('Столбец задаётся буквами Excel, например A или BC')
    query = args.get('query', '').casefold()
    sheet_filter = args.get('sheet', '')
    result, sheets, headers = [], [], {}
    count, output_size = 0, 0
    for sheet, number, cells in table_rows(name, content):
        if sheet not in sheets:
            sheets.append(sheet)
            headers[sheet] = {key: value[:200] for key, value in list(cells.items())[:50]}
        if sheet_filter and sheet_filter != sheet:
            continue
        if query and not any(query in value.casefold() for value in cells.values()):
            continue
        if not all(matches(cells.get(item['column'], ''), item['operator'], item['value']) for item in filters):
            continue
        count += 1
        if count <= offset or len(result) >= limit or output_size >= 30000:
            continue
        selected = columns or list(cells)[:50]
        values = {column: cells.get(column, '')[:500] for column in selected}
        output_size += sum(map(len, values.values()))
        result.append({'sheet': sheet, 'row': number, 'cells': values})
    return {'sheets': sheets, 'first_rows': headers, 'rows': result, 'matched': count,
            'offset': offset, 'next_offset': offset + len(result) if offset + len(result) < count and result else None,
            'notice': 'Фильтры объединены через И. gt/gte/lt/lte сравнивают числа; формулы не пересчитываются. Значения ячеек ограничены 500 символами.'}


def selected_table_rows(name, content, args):
    for sheet, number, cells in table_rows(name, content):
        if args.get('sheet') and args['sheet'] != sheet:
            continue
        if number <= args.get('header_row', 0):
            continue
        if args.get('query') and not any(args['query'].casefold() in value.casefold() for value in cells.values()):
            continue
        if all(matches(cells.get(item['column'], ''), item['operator'], item['value']) for item in args.get('filters', [])):
            yield sheet, number, cells


def inspect_table(name, content, args):
    sheets = {}
    for sheet, number, cells in table_rows(name, content):
        info = sheets.setdefault(sheet, {'rows': 0, 'last_row': 0, 'columns': set(), 'sample': []})
        info['rows'] += 1
        info['last_row'] = max(number, info['last_row'])
        info['columns'].update(cells)
        if len(info['sample']) < 3:
            info['sample'].append({'row': number, 'cells': {key: value[:200] for key, value in list(cells.items())[:30]}})
    return {'sheets': [{'name': sheet, **info, 'columns': sorted(info['columns'], key=lambda value: (len(value), value))[:100]}
                       for sheet, info in list(sheets.items())[:30]], 'sheet_count': len(sheets),
            'notice': 'rows — число записанных строк, last_row — последний номер. Пустые строки/ячейки могут отсутствовать. Показаны до 30 листов.'}


def numeric_value(value):
    try:
        number = float(value.split(' [формула:', 1)[0].strip().replace(' ', '').replace('\xa0', '').replace(',', '.'))
        return number if math.isfinite(number) else None
    except ValueError:
        return None


def summarize_table(name, content, args):
    groups = {}
    total_rows = skipped = 0
    for sheet, number, cells in selected_table_rows(name, content, args):
        total_rows += 1
        group = cells.get(args.get('group_by'), '') if args.get('group_by') else 'Все строки'
        if group not in groups and len(groups) >= 200:
            skipped += 1
            continue
        stat = groups.setdefault(group, {'rows': 0, 'empty': 0, 'numeric_count': 0, 'sum': 0, 'min': None, 'max': None})
        value = cells.get(args['column'], '')
        stat['rows'] += 1
        stat['empty'] += not value.strip()
        number = numeric_value(value)
        if number is not None:
            stat['numeric_count'] += 1
            stat['sum'] += number
            stat['min'] = number if stat['min'] is None else min(stat['min'], number)
            stat['max'] = number if stat['max'] is None else max(stat['max'], number)
    for stat in groups.values():
        stat['average'] = stat['sum'] / stat['numeric_count'] if stat['numeric_count'] else None
        if not stat['numeric_count']:
            stat['sum'] = None
    return {'matched_rows': total_rows, 'groups': [{'group': key[:500], **value} for key, value in groups.items()],
            'skipped_rows_due_to_group_limit': skipped, 'notice': 'Нечисловые значения не входят в сумму/среднее. Формулы не пересчитываются; даты Excel могут быть числами.'}


def export_table(name, content, args):
    rows = list(selected_table_rows(name, content, args))
    sort_by = args.get('sort_by', '')
    if sort_by:
        def key(row):
            value = row[2].get(sort_by, '')
            number = numeric_value(value) if args.get('numeric_sort') else None
            return (number is None, number if number is not None else value.casefold())
        rows.sort(key=key, reverse=args.get('descending', False))
    columns = args.get('columns') or sorted({column for _, _, cells in rows for column in cells}, key=lambda value: (len(value), value))
    if len(columns) > 200:
        raise ValueError('Выберите не более 200 колонок для экспорта')
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['source_sheet', 'source_row', *columns])
    for sheet, number, cells in rows:
        values = [sheet, str(number), *(cells.get(column, '') for column in columns)]
        # Выгрузка содержит значения, а не исполняемые формулы CSV.
        writer.writerow(["'" + value if value.startswith(('=', '+', '-', '@')) and numeric_value(value) is None else value for value in values])
        if stream.tell() > 10 * 1024 * 1024:
            raise ValueError('Результат больше 10 МБ; сузьте фильтры или набор колонок')
    data = stream.getvalue().encode('utf-8-sig')
    if len(data) > 10 * 1024 * 1024:
        raise ValueError('Результат больше 10 МБ')
    return data, len(rows)
