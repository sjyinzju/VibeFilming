import { useSyncExternalStore } from 'react';
import { load, save } from './state/storage';
import zh from './locales/zh.json';

export type Language = 'zh' | 'en';
let language: Language = load<string>('language', 'zh') === 'en' ? 'en' : 'zh';
const listeners = new Set<() => void>();
const dictionary: Record<string, string> = zh;
const folded = Object.fromEntries(
  Object.entries(dictionary).map(([key, value]) => [key.toLowerCase(), value]),
);
export const getLanguage = () => language;
export function setLanguage(next: Language) {
  language = next;
  save('language', next);
  document.documentElement.lang = next === 'zh' ? 'zh-CN' : 'en';
  document.title = next === 'zh' ? 'Movie Agent · 电影创作工作室' : 'Movie Agent — Web Studio';
  listeners.forEach((listener) => listener());
}
export function useLocale() {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => language,
  );
}
/** Only presentation text is passed here. User content and raw contracts stay unchanged. */
export function t(text: string | null | undefined): string {
  if (!text) return '';
  if (language === 'en') return text;
  const key = text.trim();
  const translated = dictionary[key] ?? folded[key.toLowerCase()];
  if (translated) return text.replace(key, translated);
  const recovered = key.match(/^Recovered (\d+) events ·$/);
  if (recovered) return `已恢复 ${recovered[1]} 条事件 · `;
  const review = key.match(/^Approve (.+)\?$/);
  if (review) return `是否批准${t(review[1])}？`;
  const remove = key.match(/^Remove (.+)$/);
  if (remove) return `删除${t(remove[1])}`;
  const count = key.match(
    /^(Character|Moment|Visual|Scene seed|Reference|SCENE SEED|SCENE|SHOT) (.+)$/,
  );
  if (count) return `${t(count[1])} ${count[2]}`;
  const summary = key.match(/^(\d+) shots · ([\d.]+)s planned$/);
  if (summary) return `${summary[1]} 个镜头 · 规划 ${summary[2]} 秒`;
  const camera = key.match(/^(.+) · ([\d.]+)(s|mm)$/);
  if (camera) return `${t(camera[1])} · ${camera[2]}${camera[3] === 's' ? '秒' : 'mm'}`;
  const fieldError = key.match(/^(.+) cannot be empty\.$/);
  if (fieldError) return `${t(fieldError[1])}不能为空。`;
  const check = key.match(/^Check (.+)\.$/);
  if (check) return `请检查${t(check[1])}。`;
  const failure = key.match(/^Production failed: (.+)$/);
  if (failure) return `制作失败：${t(failure[1])}`;
  return text;
}
export function localeDate(value: string, timeOnly = false) {
  const date = new Date(value);
  return timeOnly
    ? date.toLocaleTimeString(language === 'zh' ? 'zh-CN' : 'en-US')
    : date.toLocaleString(language === 'zh' ? 'zh-CN' : 'en-US');
}
