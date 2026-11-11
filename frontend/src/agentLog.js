export function latestAgentEvents(events = []) {
  const action = events.findLast((event) => event.kind !== 'note' && event.kind !== 'summary');
  const explanation = events.findLast((event) => event.kind === 'note' || event.kind === 'summary');
  return [action, explanation].filter(Boolean).sort((a, b) => a.id - b.id);
}
