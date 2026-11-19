import { request } from './api';

export const MAX_FILE_SIZE = 25 * 1024 * 1024;
export const CHUNK_SIZE = 512 * 1024;

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

function validateFile(file) {
  if (!file) throw new Error('Файл не выбран');
  if (file.size === 0) throw new Error(`Файл «${file.name}» пустой (0 байт)`);
  if (file.size > MAX_FILE_SIZE) throw new Error(`Файл «${file.name}» превышает максимальный размер 25 МБ`);
}

function blobBase64(blob, fileName) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const raw = typeof reader.result === 'string' ? reader.result : '';
      const marker = ';base64,';
      const idx = raw.indexOf(marker);
      const base64 = idx >= 0 ? raw.slice(idx + marker.length) : raw.slice(raw.indexOf(',') + 1);
      if (!base64) {
        reject(new Error(`Не удалось прочитать файл «${fileName}»`));
        return;
      }
      resolve(base64);
    };
    reader.onerror = () => reject(new Error(`Не удалось прочитать файл «${fileName}»`));
    reader.readAsDataURL(blob);
  });
}

export function fileContent(file) {
  try {
    validateFile(file);
  } catch (error) {
    return Promise.reject(error);
  }
  return blobBase64(file, file.name);
}

export function cancelStagedUpload(uploadId) {
  if (!uploadId) return Promise.resolve();
  return request(`/integrations/uploads/${uploadId}`, { method: 'DELETE' }).catch(() => {});
}

async function uploadInChunks(file, payload) {
  let uploadId = null;
  let index = 0;
  try {
    for (let offset = 0; offset < file.size; offset += CHUNK_SIZE) {
      const end = Math.min(file.size, offset + CHUNK_SIZE);
      const content = await blobBase64(file.slice(offset, end), file.name);
      const result = await request('/integrations/uploads/chunk', {
        method: 'POST',
        body: JSON.stringify({
          ...payload,
          upload_id: uploadId,
          index,
          content,
          done: end >= file.size,
        }),
      });
      if (end >= file.size) return result;
      uploadId = result.upload_id;
      index += 1;
    }
    return null;
  } catch (error) {
    if (uploadId && error?.status !== 409) await cancelStagedUpload(uploadId);
    throw error;
  }
}

export async function uploadTaskFile(taskId, file) {
  validateFile(file);
  const name = safeFileName(file.name);
  if (file.size <= CHUNK_SIZE) {
    return request(`/integrations/tasks/${taskId}/monocle/files`, {
      method: 'POST',
      body: JSON.stringify({ name, content: await blobBase64(file, file.name) }),
    });
  }
  return uploadInChunks(file, { target: 'task', task_id: taskId, name });
}

export async function uploadTaskFiles(taskId, files) {
  for (let index = 0; index < files.length; index += 1) {
    try {
      await uploadTaskFile(taskId, files[index]);
    } catch (error) {
      error.remainingFiles = files.slice(index);
      throw error;
    }
  }
}

export async function uploadWorkspaceFile(path, file, { overwrite = false, uploadId = null } = {}) {
  if (uploadId) {
    try {
      return await request('/integrations/uploads/chunk', {
        method: 'POST',
        body: JSON.stringify({ upload_id: uploadId, done: true, target: 'workspace', path, overwrite: true }),
      });
    } catch (error) {
      if (error?.status !== 410) throw error;
    }
  }
  validateFile(file);
  if (file.size <= CHUNK_SIZE) {
    return request('/integrations/monocle/workspace/upload', {
      method: 'POST',
      body: JSON.stringify({ path, content: await blobBase64(file, file.name), overwrite }),
    });
  }
  return uploadInChunks(file, { target: 'workspace', path, overwrite });
}
