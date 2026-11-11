// Одна палитра для календаря и таймлайна Ameba.
export const calendarColors = {
  task: '#7f49b4', busy: '#326b94', sleep: '#42465d', meal: '#8a6737',
  break: '#367567', work: '#45513e', personal: '#454545',
};

export function calendarBlockColor(kind, temporary = false) {
  const color = calendarColors[kind] ?? calendarColors.busy;
  return temporary && kind !== 'task' ? `${color}80` : color;
}
