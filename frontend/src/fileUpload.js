import { request } from './api';

export function fileContent(file) {
  if (file.size > 10 * 1024 * 1024) return Promise.reject(new Error('Максимальный размер файла — 10 МБ'));
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(',')[1]);
    reader.onerror = () => reject(new Error('Не удалось прочитать файл'));
    reader.readAsDataURL(file);
  });
}

export async function uploadTaskFiles(taskId, files) {
  for (let index = 0; index < files.length; index += 1) {
    const file = files[index];
    try {
      await request(`/integrations/tasks/${taskId}/monocle/files`, {
        method: 'POST', body: JSON.stringify({ name: file.name, content: await fileContent(file) }),
      });
    } catch (error) {
      error.remainingFiles = files.slice(index);
      throw error;
    }
  }
}
