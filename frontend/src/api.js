export async function request(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json' },
  });
  if (!response.ok) {
    const error = await response.json();
    const failure = new Error(typeof error.detail === 'string' ? error.detail : 'Проверьте введённые данные');
    failure.status = response.status;
    throw failure;
  }
  return response.status === 204 ? null : response.json();
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
