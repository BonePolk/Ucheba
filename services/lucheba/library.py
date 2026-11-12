"""Одинаковое чтение личных файлов и вложений для Monocle и Straus."""
import asyncio
import base64

from fastapi import HTTPException
import materials
import workspace
from memory import Memory


async def run(name, args):
    memory = await asyncio.to_thread(Memory)
    content = []
    if name == 'save_browser_download':
        original = base64.b64decode(args['content'], validate=True)
        output = await asyncio.to_thread(workspace.save_browser_download, memory, args, original)
    elif name == 'load_browser_download':
        info, original = await asyncio.to_thread(workspace.load_browser_download, memory, args['file_id'])
        output = {'info': info, 'content': base64.b64encode(original).decode()}
    elif name == 'list_browser_downloads':
        output = await asyncio.to_thread(workspace.browser_downloads, memory)
    elif name in ('search_table', 'inspect_table', 'summarize_table', 'export_table'):
        from common.office import search_table, inspect_table, summarize_table, export_table
        if args.get('silo_path'):
            original, version = await asyncio.to_thread(workspace.read_binary, memory, args['silo_path'])
            filename = args['silo_path'].rsplit('/', 1)[-1]
        else:
            task_id, file_id = args['source_task_id'], args['file_id']
            info = await asyncio.to_thread(materials.get_info, memory, task_id, file_id)
            original = await asyncio.to_thread(memory.read_bytes, materials.file_key(task_id, file_id) + '.bin')
            filename = info['name']
        if name == 'export_table':
            if not args['output_path'].lower().endswith('.csv'):
                raise HTTPException(422, 'output_path должен оканчиваться .csv')
            data, count = await asyncio.to_thread(export_table, filename, original, args)
            output = await asyncio.to_thread(workspace.save_binary, memory, args['output_path'], data)
            output['rows'] = count
        else:
            operation = {'search_table': search_table, 'inspect_table': inspect_table, 'summarize_table': summarize_table}[name]
            output = await asyncio.to_thread(operation, filename, original, args)
    elif name == 'read_silo_file':
        original, version = await asyncio.to_thread(workspace.read_binary, memory, args['path'])
        return {'output': {'path': args['path'], 'version': version},
                'file': {'name': args['path'].rsplit('/', 1)[-1], 'content': base64.b64encode(original).decode()}}
    elif name == 'save_file_to_silo':
        original = base64.b64decode(args['content'], validate=True)
        output = await asyncio.to_thread(workspace.save_binary, memory, args['path'], original, args.get('version'))
    elif name == 'write_silo_file':
        output = await asyncio.to_thread(workspace.save, memory, workspace.personal_path(args['path']), args['text'], args.get('version') or None)
        output['path'] = args['path']
    elif name == 'delete_silo_file':
        output = await asyncio.to_thread(workspace.delete_file, memory, args['path'])
    elif name == 'zip_files':
        output = await asyncio.to_thread(workspace.zip_files, memory, args['paths'], args['output_path'])
    elif name == 'list_user_files':
        output = await asyncio.to_thread(workspace.tree, memory, False)
    elif name == 'read_user_file':
        workspace.personal_path(args['path'])
        data = await asyncio.to_thread(workspace.read, memory, args['path'])
        output = {'path': args['path'], 'version': data['version'], 'content': data['content'][:materials.TEXT_LIMIT], 'truncated': len(data['content']) > materials.TEXT_LIMIT}
    elif name == 'list_memory':
        output = await asyncio.to_thread(memory.list_files)
    elif name == 'read_memory':
        output = await asyncio.to_thread(memory.read, args['filename'])
    elif name == 'list_saved_files':
        output = await asyncio.to_thread(materials.saved_files, memory, args.get('query', ''))
    elif name in ('read_file', 'view_pdf_page'):
        task_id, file_id = args['source_task_id'], args['file_id']
        info = await asyncio.to_thread(materials.get_info, memory, task_id, file_id)
        if name == 'view_pdf_page':
            content = await asyncio.to_thread(materials.pdf_page, memory, task_id, file_id, args['page'])
        else:
            content = await asyncio.to_thread(materials.file_input, memory, task_id, file_id)
        output = {'status': 'read_file', 'file': materials.public_info(info),
                  'url': f'/api/integrations/context/files/{task_id}/{file_id}'}
    else:
        raise HTTPException(422, 'Неизвестный инструмент библиотеки')
    return {'output': output, 'content': content}
