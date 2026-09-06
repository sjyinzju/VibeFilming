import { setLanguage, type Language } from '../i18n';

export function LanguageSwitch({ language }: { language: Language }) {
  return (
    <select
      className="language-switch"
      aria-label="界面语言 / Interface language"
      title="界面语言 / Interface language"
      value={language}
      onChange={(event) => setLanguage(event.target.value as Language)}
    >
      <option value="zh">中文</option>
      <option value="en">English</option>
    </select>
  );
}
