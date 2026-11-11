"""Чтение значений XLSX без запуска Excel и формул."""
import re
from xml.etree import ElementTree

def spreadsheet_text(zipped):
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
    lines = []
    for path in sorted(zipped.namelist()):
        if not re.fullmatch(r'xl/worksheets/[^/]+\.xml', path):
            continue
        lines.append('Лист: ' + sheet_names.get(path, path))
        sheet = ElementTree.fromstring(zipped.read(path))
        for row in sheet.findall('x:sheetData/x:row', ns):
            cells = []
            for cell in row.findall('x:c', ns):
                value = cell.findtext('x:v', default='', namespaces=ns)
                if cell.get('t') == 's':
                    index = int(value)
                    value = strings[index] if 0 <= index < len(strings) else '[неизвестная строка]'
                elif cell.get('t') == 'inlineStr':
                    value = ''.join(cell.find('x:is', ns).itertext())
                elif cell.get('t') == 'b':
                    value = 'TRUE' if value == '1' else 'FALSE'
                formula = cell.findtext('x:f', namespaces=ns)
                if formula:
                    value = value + ' [формула: ' + formula + '; сохранённое значение]'
                cells.append(cell.get('r', '') + ': ' + value)
            lines.append(' | '.join(cells))
            if sum(map(len, lines)) > 40000:
                return '\n'.join(lines)[:40000]
    return '\n'.join(lines)[:40000]

