"""Чтение значений XLSX без запуска Excel и формул."""
import csv
import io
import math
from pathlib import Path
import posixpath
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
    if suffix in ('.xlsx', '.ods'):
        with ZipFile(io.BytesIO(content)) as zipped:
            if len(zipped.infolist()) > 2000 or sum(item.file_size for item in zipped.infolist()) > 20 * 1024 * 1024:
                raise ValueError('Слишком большой распакованный XLSX')
            yield from ods_rows(zipped) if suffix == '.ods' else spreadsheet_rows(zipped)
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
        raise ValueError('Поиск поддерживает XLSX, ODS, CSV и TSV. Для XLS нужен экспорт в XLSX/CSV')


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


def ods_paragraph(node):
    text = node.text or ''
    namespace = 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'
    for child in node:
        if child.tag == '{' + namespace + '}s':
            text += ' ' * min(int(child.get('{' + namespace + '}c', '1')), 1000)
        elif child.tag == '{' + namespace + '}tab':
            text += '\t'
        elif child.tag == '{' + namespace + '}line-break':
            text += '\n'
        else:
            text += ods_paragraph(child)
        text += child.tail or ''
    return text


def ods_rows(zipped):
    ns = {'t': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
          'o': 'urn:oasis:names:tc:opendocument:xmlns:office:1.0',
          'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'}
    document = ElementTree.fromstring(zipped.read('content.xml'))
    for table in document.findall('.//t:table', ns):
        name = table.get('{' + ns['t'] + '}name', 'Лист')
        number = 1
        for row in table.findall('.//t:table-row', ns):
            repeated = int(row.get('{' + ns['t'] + '}number-rows-repeated', '1'))
            cells, column = {}, 1
            for cell in row:
                if cell.tag not in ('{' + ns['t'] + '}table-cell', '{' + ns['t'] + '}covered-table-cell'):
                    continue
                count = int(cell.get('{' + ns['t'] + '}number-columns-repeated', '1'))
                value = '\n'.join(ods_paragraph(p) for p in cell.findall('text:p', ns))
                for attribute in ('date-value', 'time-value', 'value', 'boolean-value', 'string-value'):
                    raw_value = cell.get('{' + ns['o'] + '}' + attribute)
                    if raw_value is not None:
                        value = raw_value
                        break
                if value:
                    if column + count > 16385:
                        raise ValueError('Слишком много непустых столбцов ODS')
                    for index in range(column, column + count):
                        cells[column_name(index)] = value
                column += count
            if cells:
                if number + repeated > 1048577:
                    raise ValueError('Слишком много непустых строк ODS')
                for index in range(number, number + repeated):
                    yield name, index, cells
            number += repeated


def table_text(name, content):
    lines, size, previous = [], 0, None
    for sheet, number, cells in table_rows(name, content):
        if sheet != previous:
            lines.append('Лист: ' + sheet)
            previous = sheet
        line = ' | '.join(f'{column}{number}: {value}' for column, value in cells.items())
        lines.append(line)
        size += len(line)
        if size > 40000:
            break
    return '\n'.join(lines)[:40000]


# --- Структурный разбор DOCX (OOXML) ---

_W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
_R_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
_A_NS = 'http://schemas.openxmlformats.org/drawingml/2006/main'
_WP_NS = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
_V_NS = 'urn:schemas-microsoft-com:vml'
_REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'

_DOCX_NS = {
    'w': _W_NS,
    'r': _R_NS,
    'a': _A_NS,
    'wp': _WP_NS,
    'v': _V_NS,
    'rel': _REL_NS,
}

_DOCX_MAX_BLOCKS = 1200
_DOCX_MAX_CHARS = 250000
_DOCX_MAX_IMAGES = 25
_DOCX_MAX_IMAGE_B64_BYTES = 4 * 1024 * 1024
_MONO_FONTS = ('courier', 'consolas', 'monaco', 'mono', 'menlo')


def _w_attr(element, name, default=None):
    if element is None:
        return default
    return element.get(f'{{{_W_NS}}}{name}', default)


def _w_tag(element):
    tag = element.tag
    if tag.startswith(f'{{{_W_NS}}}'):
        return tag[len(_W_NS) + 2:]
    return tag


def _bool_prop(rpr, tag_name):
    if rpr is None:
        return False
    prop = rpr.find(f'w:{tag_name}', _DOCX_NS)
    if prop is None:
        return False
    val = _w_attr(prop, 'val')
    return val is None or val.lower() not in ('0', 'false', 'off')


def _norm_docx_target(target):
    if not target:
        return None
    cleaned = target.replace('\\', '/').lstrip('/')
    if not cleaned.startswith('word/'):
        cleaned = 'word/' + cleaned
    normalized = posixpath.normpath(cleaned)
    if normalized == '.' or normalized.startswith('../') or '/..' in normalized or normalized == '..':
        return None
    return normalized


def _parse_docx_rels(zipped):
    hyperlinks = {}
    image_targets = {}
    if 'word/_rels/document.xml.rels' not in zipped.namelist():
        return hyperlinks, image_targets
    try:
        root = ElementTree.fromstring(zipped.read('word/_rels/document.xml.rels'))
    except ElementTree.ParseError:
        return hyperlinks, image_targets
    for rel in root:
        if rel.tag != f'{{{_REL_NS}}}Relationship' and not rel.tag.endswith('}Relationship'):
            continue
        rid = rel.get('Id', '')
        rel_type = rel.get('Type', '')
        target = rel.get('Target', '')
        mode = rel.get('TargetMode', '')
        if not rid or not target:
            continue
        if mode == 'External' and rel_type.endswith('/hyperlink'):
            if target.startswith(('http://', 'https://')):
                hyperlinks[rid] = target
        elif mode != 'External' and rel_type.endswith('/image'):
            norm = _norm_docx_target(target)
            if norm:
                image_targets[rid] = norm
    return hyperlinks, image_targets


def _normalize_align(val):
    if val in ('left', 'center', 'right'):
        return val
    if val == 'both':
        return 'justify'
    return None


def _parse_docx_styles(zipped):
    styles = {}
    if 'word/styles.xml' not in zipped.namelist():
        return styles
    try:
        root = ElementTree.fromstring(zipped.read('word/styles.xml'))
    except ElementTree.ParseError:
        return styles
    for style in root.findall('w:style', _DOCX_NS):
        style_id = _w_attr(style, 'styleId')
        if not style_id:
            continue
        name_el = style.find('w:name', _DOCX_NS)
        based_el = style.find('w:basedOn', _DOCX_NS)
        ppr = style.find('w:pPr', _DOCX_NS)
        outline_el = ppr.find('w:outlineLvl', _DOCX_NS) if ppr is not None else None
        jc_el = ppr.find('w:jc', _DOCX_NS) if ppr is not None else None
        outline_lvl = None
        if outline_el is not None:
            raw_lvl = _w_attr(outline_el, 'val')
            if raw_lvl is not None and raw_lvl.isdigit():
                val = int(raw_lvl)
                if 0 <= val <= 5:
                    outline_lvl = val
        styles[style_id] = {
            'name': _w_attr(name_el, 'val', ''),
            'based_on': _w_attr(based_el, 'val'),
            'outline_lvl': outline_lvl,
            'align': _normalize_align(_w_attr(jc_el, 'val')),
        }
    return styles


def _heading_from_name(text):
    if not text:
        return None
    low = text.strip().lower()
    if low in ('title', 'название'):
        return 1
    if low in ('subtitle', 'подзаголовок'):
        return 2
    match = re.search(r'(?:heading|заголовок)\s*([1-6])', low)
    if match:
        return int(match.group(1))
    return None


def _resolve_style(styles, style_id):
    heading_level = None
    align = None
    current_id = style_id
    visited = set()
    for _ in range(8):
        if not current_id or current_id in visited:
            break
        visited.add(current_id)
        if heading_level is None:
            heading_level = _heading_from_name(current_id)
        entry = styles.get(current_id)
        if not entry:
            break
        if entry['outline_lvl'] is not None and heading_level is None:
            heading_level = entry['outline_lvl'] + 1
        if heading_level is None:
            heading_level = _heading_from_name(entry['name'])
        if align is None and entry['align']:
            align = entry['align']
        if heading_level is not None and align is not None:
            break
        current_id = entry.get('based_on')
    return heading_level, align


def _parse_docx_numbering(zipped):
    abstract_map = {}
    num_map = {}
    if 'word/numbering.xml' not in zipped.namelist():
        return num_map
    try:
        root = ElementTree.fromstring(zipped.read('word/numbering.xml'))
    except ElementTree.ParseError:
        return num_map
    for abs_el in root.findall('w:abstractNum', _DOCX_NS):
        abs_id = _w_attr(abs_el, 'abstractNumId')
        if not abs_id:
            continue
        levels = {}
        for lvl in abs_el.findall('w:lvl', _DOCX_NS):
            ilvl = _w_attr(lvl, 'ilvl', '0')
            fmt = _w_attr(lvl.find('w:numFmt', _DOCX_NS), 'val', 'decimal')
            levels[ilvl] = fmt
        abstract_map[abs_id] = levels
    for num_el in root.findall('w:num', _DOCX_NS):
        num_id = _w_attr(num_el, 'numId')
        abs_id = _w_attr(num_el.find('w:abstractNumId', _DOCX_NS), 'val')
        if not num_id:
            continue
        levels = dict(abstract_map.get(abs_id, {}))
        for ovr in num_el.findall('w:lvlOverride', _DOCX_NS):
            lvl = ovr.find('w:lvl', _DOCX_NS)
            if lvl is not None:
                ilvl = _w_attr(lvl, 'ilvl') or _w_attr(ovr, 'ilvl', '0')
                fmt = _w_attr(lvl.find('w:numFmt', _DOCX_NS), 'val')
                if fmt:
                    levels[ilvl] = fmt
        num_map[num_id] = levels
    return num_map


class _DocxParser:
    def __init__(self, zipped, image_converter):
        self.zipped = zipped
        self.namelist = set(zipped.namelist())
        self.image_converter = image_converter
        self.hyperlinks, self.image_targets = _parse_docx_rels(zipped)
        self.styles = _parse_docx_styles(zipped)
        self.numbering = _parse_docx_numbering(zipped)
        self.block_count = 0
        self.char_count = 0
        self.image_count = 0
        self.image_b64_bytes = 0
        self.image_cache = {}
        self.truncated = False

    def _hit_limit(self):
        return self.truncated or self.block_count >= _DOCX_MAX_BLOCKS or self.char_count >= _DOCX_MAX_CHARS

    def _convert_image(self, rid):
        if not self.image_converter or not rid:
            return None
        if self.image_count >= _DOCX_MAX_IMAGES or self.image_b64_bytes >= _DOCX_MAX_IMAGE_B64_BYTES:
            return None
        target = self.image_targets.get(rid)
        if not target or target not in self.namelist:
            return None
        if rid in self.image_cache:
            cached = self.image_cache[rid]
            if cached is None:
                return None
            mime, b64 = cached
        else:
            try:
                raw = self.zipped.read(target)
                converted = self.image_converter(raw)
            except Exception:
                converted = None
            if not converted or not isinstance(converted, tuple) or len(converted) != 2:
                self.image_cache[rid] = None
                return None
            mime, b64 = converted
            self.image_cache[rid] = (mime, b64)
        if self.image_b64_bytes + len(b64) > _DOCX_MAX_IMAGE_B64_BYTES:
            return None
        self.image_count += 1
        self.image_b64_bytes += len(b64)
        return mime, b64

    def _run_props(self, r_el, href):
        rpr = r_el.find('w:rPr', _DOCX_NS)
        props = {}
        if _bool_prop(rpr, 'b'):
            props['bold'] = True
        if _bool_prop(rpr, 'i'):
            props['italic'] = True
        if rpr is not None:
            u_el = rpr.find('w:u', _DOCX_NS)
            if u_el is not None and (_w_attr(u_el, 'val', 'single') or 'single').lower() != 'none':
                props['underline'] = True
        if _bool_prop(rpr, 'strike'):
            props['strike'] = True
        if rpr is not None:
            fonts = rpr.find('w:rFonts', _DOCX_NS)
            if fonts is not None:
                font_vals = ' '.join(
                    (_w_attr(fonts, attr, '') or '').lower()
                    for attr in ('ascii', 'hAnsi', 'cs', 'eastAsia')
                )
                if any(token in font_vals for token in _MONO_FONTS):
                    props['code'] = True
            vert = rpr.find('w:vertAlign', _DOCX_NS)
            vert_val = _w_attr(vert, 'val')
            if vert_val == 'superscript':
                props['sup'] = True
            elif vert_val == 'subscript':
                props['sub'] = True
        if href:
            props['href'] = href
        return props

    def _extract_image_from_node(self, node):
        if not self.image_converter:
            return None
        alt = ''
        doc_pr = node.find('.//wp:docPr', _DOCX_NS)
        if doc_pr is not None:
            alt = (doc_pr.get('descr') or doc_pr.get('name') or '').strip()[:200]
        rid = None
        blip = node.find('.//a:blip', _DOCX_NS)
        if blip is not None:
            rid = blip.get(f'{{{_R_NS}}}embed') or blip.get(f'{{{_R_NS}}}link')
        if not rid:
            img_data = node.find('.//v:imagedata', _DOCX_NS)
            if img_data is not None:
                rid = img_data.get(f'{{{_R_NS}}}id')
        converted = self._convert_image(rid)
        if not converted:
            return None
        mime, b64 = converted
        img_obj = {'mime': mime, 'data': b64}
        if alt:
            img_obj['alt'] = alt
        return img_obj

    def _append_text_run(self, runs, text, props):
        if not text:
            return
        self.char_count += len(text)
        if runs and 'text' in runs[-1]:
            last = runs[-1]
            same_style = (
                len(last) == len(props) + 1
                and all(last.get(k) == v for k, v in props.items())
            )
            if same_style:
                last['text'] += text
                return
        entry = {'text': text}
        entry.update(props)
        runs.append(entry)

    def _collect_inline(self, container, segments, current_runs, href=None):
        for child in container:
            tag = _w_tag(child)
            if tag in ('del', 'bookmarkStart', 'bookmarkEnd', 'proofErr', 'pPr'):
                continue
            if tag == 'hyperlink':
                rid = child.get(f'{{{_R_NS}}}id')
                next_href = self.hyperlinks.get(rid, href)
                self._collect_inline(child, segments, current_runs, href=next_href)
            elif tag in ('ins', 'smartTag'):
                self._collect_inline(child, segments, current_runs, href=href)
            elif tag == 'sdt':
                sdt_content = child.find('w:sdtContent', _DOCX_NS)
                if sdt_content is not None:
                    self._collect_inline(sdt_content, segments, current_runs, href=href)
            elif tag == 'r':
                props = self._run_props(child, href)
                for node in child:
                    node_tag = _w_tag(node)
                    if node_tag in ('rPr', 'delText'):
                        continue
                    if node_tag == 't':
                        self._append_text_run(current_runs, node.text or '', props)
                    elif node_tag == 'tab':
                        self._append_text_run(current_runs, '    ', props)
                    elif node_tag == 'noBreakHyphen':
                        self._append_text_run(current_runs, '-', props)
                    elif node_tag in ('br', 'cr'):
                        if node_tag == 'br' and _w_attr(node, 'type') == 'page':
                            if current_runs:
                                segments.append(('runs', list(current_runs)))
                                current_runs.clear()
                            segments.append(('page_break', None))
                        else:
                            current_runs.append({'br': True})
                    elif node_tag in ('drawing', 'pict'):
                        image_obj = self._extract_image_from_node(node)
                        if image_obj:
                            current_runs.append({'image': image_obj})

    def _parse_paragraph(self, p_el):
        ppr = p_el.find('w:pPr', _DOCX_NS)
        style_id = _w_attr(ppr.find('w:pStyle', _DOCX_NS), 'val') if ppr is not None else None
        style_heading, style_align = _resolve_style(self.styles, style_id)

        heading_level = None
        if ppr is not None:
            outline_el = ppr.find('w:outlineLvl', _DOCX_NS)
            if outline_el is not None:
                raw_lvl = _w_attr(outline_el, 'val')
                if raw_lvl is not None and raw_lvl.isdigit() and 0 <= int(raw_lvl) <= 5:
                    heading_level = int(raw_lvl) + 1
        if heading_level is None:
            heading_level = style_heading

        align = _normalize_align(_w_attr(ppr.find('w:jc', _DOCX_NS), 'val') if ppr is not None else None)
        if align is None:
            align = style_align

        list_info = None
        if heading_level is None and ppr is not None:
            num_pr = ppr.find('w:numPr', _DOCX_NS)
            if num_pr is not None:
                num_id = _w_attr(num_pr.find('w:numId', _DOCX_NS), 'val')
                if num_id and num_id != '0':
                    ilvl_raw = _w_attr(num_pr.find('w:ilvl', _DOCX_NS), 'val', '0')
                    ilvl = int(ilvl_raw) if ilvl_raw.isdigit() else 0
                    num_fmt = self.numbering.get(num_id, {}).get(str(ilvl), 'decimal')
                    list_info = {
                        'ordered': num_fmt not in ('bullet', 'none'),
                        'level': max(0, min(ilvl, 8)),
                    }

        segments = []
        current_runs = []
        self._collect_inline(p_el, segments, current_runs)
        if current_runs or not segments:
            segments.append(('runs', current_runs))

        blocks = []
        for seg_type, seg_val in segments:
            if seg_type == 'page_break':
                blocks.append({'type': 'page_break'})
                continue
            runs = seg_val
            image_runs = [item['image'] for item in runs if 'image' in item]
            has_text = any(item.get('text', '').strip() for item in runs if 'text' in item)
            if len(image_runs) == 1 and not has_text and not any(item.get('br') for item in runs):
                img = dict(image_runs[0])
                block = {'type': 'image', 'mime': img['mime'], 'data': img['data']}
                if 'alt' in img:
                    block['alt'] = img['alt']
                blocks.append(block)
            elif heading_level is not None and (has_text or image_runs):
                block = {'type': 'heading', 'level': heading_level, 'runs': runs}
                if align and align != 'left':
                    block['align'] = align
                blocks.append(block)
            elif list_info is not None and (has_text or image_runs):
                blocks.append({
                    'type': 'list_item',
                    'ordered': list_info['ordered'],
                    'level': list_info['level'],
                    'runs': runs,
                })
            else:
                block = {'type': 'paragraph', 'runs': runs}
                if align and align != 'left':
                    block['align'] = align
                blocks.append(block)
        return blocks

    def _parse_table(self, tbl_el):
        tbl_pr = tbl_el.find('w:tblPr', _DOCX_NS)
        tbl_look = tbl_pr.find('w:tblLook', _DOCX_NS) if tbl_pr is not None else None
        allow_first_row = True
        if tbl_look is not None:
            first_row_attr = _w_attr(tbl_look, 'firstRow')
            if first_row_attr in ('0', 'false', 'off'):
                allow_first_row = False

        tr_elements = tbl_el.findall('w:tr', _DOCX_NS)
        rows = []
        active_vmerge = {}
        for row_idx, tr in enumerate(tr_elements):
            if self._hit_limit():
                self.truncated = True
                break
            tr_pr = tr.find('w:trPr', _DOCX_NS)
            explicit_header = tr_pr is not None and tr_pr.find('w:tblHeader', _DOCX_NS) is not None
            is_header = explicit_header or (row_idx == 0 and allow_first_row and len(tr_elements) >= 2)
            row_cells = []
            col = 0
            merged_incremented = set()
            for tc in tr.findall('w:tc', _DOCX_NS):
                tc_pr = tc.find('w:tcPr', _DOCX_NS)
                col_span = 1
                if tc_pr is not None:
                    span_el = tc_pr.find('w:gridSpan', _DOCX_NS)
                    raw_span = _w_attr(span_el, 'val')
                    if raw_span and raw_span.isdigit():
                        col_span = max(1, min(int(raw_span), 50))
                vmerge_el = tc_pr.find('w:vMerge', _DOCX_NS) if tc_pr is not None else None
                if vmerge_el is not None:
                    v_val = _w_attr(vmerge_el, 'val')
                    if v_val == 'restart':
                        cell_blocks = self._parse_container_blocks(tc)
                        cell = {'blocks': cell_blocks, '_start_col': col, '_row_span': 1}
                        if col_span > 1:
                            cell['colSpan'] = col_span
                        for c in range(col, col + col_span):
                            active_vmerge[c] = cell
                        row_cells.append(cell)
                    else:
                        anchor = active_vmerge.get(col)
                        if anchor is not None:
                            if id(anchor) not in merged_incremented:
                                anchor['_row_span'] += 1
                                merged_incremented.add(id(anchor))
                        else:
                            # Продолжение vMerge без предыдущего restart — отображаем как пустую ячейку.
                            cell = {'blocks': self._parse_container_blocks(tc)}
                            if col_span > 1:
                                cell['colSpan'] = col_span
                            row_cells.append(cell)
                else:
                    for c in range(col, col + col_span):
                        active_vmerge.pop(c, None)
                    cell = {'blocks': self._parse_container_blocks(tc)}
                    if col_span > 1:
                        cell['colSpan'] = col_span
                    row_cells.append(cell)
                col += col_span
            if row_cells:
                rows.append({'header': is_header, 'cells': row_cells})

        for row in rows:
            for cell in row['cells']:
                row_span = cell.pop('_row_span', 1)
                cell.pop('_start_col', None)
                if row_span > 1:
                    cell['rowSpan'] = row_span
        self.block_count += 1
        return {'type': 'table', 'rows': rows}

    def _emit_merged_block(self, blocks, block):
        if block['type'] == 'list_item':
            item = {'level': block['level'], 'runs': block['runs']}
            if blocks and blocks[-1]['type'] == 'list' and blocks[-1]['ordered'] == block['ordered']:
                blocks[-1]['items'].append(item)
            else:
                self.block_count += 1
                blocks.append({'type': 'list', 'ordered': block['ordered'], 'items': [item]})
            return
        if block['type'] == 'paragraph' and not block['runs'] and blocks and blocks[-1]['type'] == 'paragraph' and not blocks[-1]['runs']:
            return
        self.block_count += 1
        blocks.append(block)

    def _iter_container_blocks(self, container, out_blocks):
        for child in container:
            if self._hit_limit():
                self.truncated = True
                return
            tag = _w_tag(child)
            if tag in ('sectPr', 'del', 'bookmarkStart', 'bookmarkEnd', 'proofErr', 'tblPr', 'tblGrid', 'trPr', 'tcPr'):
                continue
            if tag == 'sdt':
                sdt_content = child.find('w:sdtContent', _DOCX_NS)
                if sdt_content is not None:
                    self._iter_container_blocks(sdt_content, out_blocks)
            elif tag in ('ins', 'smartTag'):
                self._iter_container_blocks(child, out_blocks)
            elif tag == 'p':
                for blk in self._parse_paragraph(child):
                    if self._hit_limit():
                        self.truncated = True
                        return
                    self._emit_merged_block(out_blocks, blk)
            elif tag == 'tbl':
                out_blocks.append(self._parse_table(child))

    def _parse_container_blocks(self, container):
        blocks = []
        self._iter_container_blocks(container, blocks)
        return blocks

    def parse(self):
        if 'word/document.xml' not in self.namelist:
            return {'blocks': [], 'truncated': False}
        try:
            root = ElementTree.fromstring(self.zipped.read('word/document.xml'))
        except ElementTree.ParseError:
            return {'blocks': [], 'truncated': False}
        body = root.find('w:body', _DOCX_NS)
        if body is None:
            return {'blocks': [], 'truncated': False}
        blocks = self._parse_container_blocks(body)
        return {'blocks': blocks, 'truncated': self.truncated}


def docx_document(zipped, image_converter=None):
    return _DocxParser(zipped, image_converter).parse()


def _runs_plain_text(runs):
    parts = []
    for run in runs:
        if 'text' in run:
            parts.append(run['text'])
        elif run.get('br'):
            parts.append('\n')
    return ''.join(parts)


def docx_plain_text(doc):
    lines = []
    for block in doc.get('blocks', []):
        btype = block.get('type')
        if btype in ('heading', 'paragraph'):
            text = _runs_plain_text(block.get('runs', [])).strip()
            if text:
                lines.append(text)
        elif btype == 'list':
            for item in block.get('items', []):
                text = _runs_plain_text(item.get('runs', [])).strip()
                if text:
                    lines.append('- ' + text)
        elif btype == 'table':
            for row in block.get('rows', []):
                row_texts = []
                for cell in row.get('cells', []):
                    cell_text = ' '.join(
                        part for part in (
                            docx_plain_text({'blocks': cell.get('blocks', [])}).replace('\n', ' ').strip(),
                        ) if part
                    )
                    row_texts.append(cell_text)
                if any(row_texts):
                    lines.append(' | '.join(row_texts))
    return '\n'.join(lines)
