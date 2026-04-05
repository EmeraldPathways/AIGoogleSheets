export function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

export function setDisabled(id, disabled) {
  const el = document.getElementById(id);
  if (el) el.disabled = disabled;
}

export function getValue(id) {
  const el = document.getElementById(id);
  return el ? el.value : '';
}
