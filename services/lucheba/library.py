"""Одинаковое чтение личных файлов и вложений для Monocle и Straus."""
import asyncio

from fastapi import HTTPException
import materials
import workspace
from memory import Memory


async def run(name, args):
    memory = await asyncio.to_thread(Memory)
    content = []
    if name == 'list_user_files':
        output = await asyncio.to_thread(workspace.tree, memory, False)
    elif name == 'read_user_file':
        workspace.personal_path(args['path'])
        data = await asyncio.to_thread(workspace.read, memory, args['path'])
        output = {'path': args['path'], 'content': data['content'][:materials.TEXT_LIMIT], 'truncated': len(data['content']) > materials.TEXT_LIMIT}
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
