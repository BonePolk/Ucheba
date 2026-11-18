export async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`/api${path}`, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...options.headers },
    });
  } catch {
    throw new Error('Не удалось отправить запрос: соединение с сервером прервалось');
  }
  if (!response.ok) {
    const text = await response.text();
    let error = null;
    if (text) {
      try {
        error = JSON.parse(text);
      } catch {
        error = null;
      }
    }
    const fallbackByStatus = {
      413: 'Файл слишком большой (макс. 25 МБ)',
      502: 'Сервис временно недоступен',
      503: 'Сервис временно недоступен',
      504: 'Превышено время ожидания сервера',
    };
    const message = typeof error?.detail === 'string' && error.detail
      ? error.detail
      : fallbackByStatus[response.status] ?? `Ошибка запроса (HTTP ${response.status})`;
    const failure = new Error(message);
    failure.status = response.status;
    throw failure;
  }
  return response.status === 204 ? null : response.json();
}


export async function agentRequest(path, options = {}) {
  const started = await request(`${path}${path.includes('?') ? '&' : '?'}background=true`, options);
  if (!started?.job_id) return started;
  while (true) {
    const job = await request(`/integrations/jobs/${started.job_id}`);
    if (job.status === 'completed') return job.result;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

export function getTasks(filters = {}) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  return request(`/tasks?${params}`);
}

export function saveTask({ id, data }) {
  return request(id ? `/tasks/${id}` : '/tasks', {
    method: id ? 'PATCH' : 'POST',
    body: JSON.stringify(data),
  });
}

export function deleteTask({ id, permanent = false }) {
  return request(`/tasks/${id}?permanent=${permanent}`, { method: 'DELETE' });
}

export const agentName = (name) => ({ Lucheba: 'Monocle', Codex: 'Arrodes', '\u0410\u0440\u0440\u043e\u0434\u0435\u0441': 'Arrodes' })[name] ?? name;
