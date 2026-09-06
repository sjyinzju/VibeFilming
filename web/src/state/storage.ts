export function load<T>(key: string, fallback: T): T {
  try {
    return JSON.parse(localStorage.getItem(`studio:v1:${key}`) || 'null') ?? fallback;
  } catch {
    return fallback;
  }
}
export function save(key: string, value: unknown) {
  try {
    localStorage.setItem(`studio:v1:${key}`, JSON.stringify(value));
  } catch {
    /* Private mode/quota: session still works. */
  }
}
