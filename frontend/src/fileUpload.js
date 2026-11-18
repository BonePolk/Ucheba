import { request } from './api';

export const MAX_FILE_SIZE = 25 * 1024 * 1024;

export function safeFileName(name, limit = 200) {
  const clean = (name || 'file').replace(/\\/g, '/').split('/').pop().trim() || 'file';
  if (clean.length <= limit) return clean;
  const dot = clean.lastIndexOf('.');
  if (dot > 0 && clean.length - dot < limit) {
    const ext = clean.slice(dot);
    return clean.slice(0, limit - ext.length) + ext;
  }
  return clean.slice(0, limit);
}

export function fileContent(file) {
  if (!file) return Promise.reject(new Error('Файл не выбран'));
  if (file.size === 0) return Promise.reject(new Error(`Файл «${file.name}» пустой (0 байт)`));
  if (file.size > MAX_FILE_SIZE) return Promise.reject(new Error(`Файл «${file.name}» превышает максимальный размер 25 МБ`));
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const raw = typeof reader.result === 'string' ? reader.result : '';
      const marker = ';base64,';
      const idx = raw.indexOf(marker);
      const base64 = idx >= 0 ? raw.slice(idx + marker.length) : raw.slice(raw.indexOf(',') + 1);
      if (!base64) {
        reject(new Error(`Не удалось прочитать файл «${file.name}»`));
        return;
      }
      resolve(base64);
    };
    reader.onerror = () => reject(new Error(`Не удалось прочитать файл «${file.name}»`));
    reader.readAsDataURL(file);
  });
}

export async function uploadTaskFiles(taskId, files) {
  for (let index = 0; index < files.length; index += 1) {
    const file = files[index];
    try {
      await request(`/integrations/tasks/${taskId}/monocle/files`, {
        method: 'POST',
        body: JSON.stringify({ name: safeFileName(file.name), content: await fileContent(file) }),
      });
    } catch (error) {
      error.remainingFiles = files.slice(index);
      throw error;
    }
  }
}
