import { render, screen, fireEvent, act } from '@testing-library/react';
import { expect, it } from 'vitest';
import { setLanguage, t, useLocale } from '../i18n';
import { LanguageSwitch } from '../components/LanguageSwitch';
import { BriefConsole, toInput } from '../components/BriefConsole';

function LocalizedBrief() {
  const language = useLocale();
  return (
    <>
      <LanguageSwitch language={language} />
      <BriefConsole
        draft={{ story_description: 'Keep my story in English.', output_language: 'en' }}
        onChange={() => {}}
        locked={false}
        busy={false}
        onStart={() => {}}
      />
    </>
  );
}
it('switches all presentation labels while preserving story and film output language', () => {
  setLanguage('zh');
  const input = toInput({ story_description: 'Keep my story in English.', output_language: 'en' });
  render(<LocalizedBrief />);
  expect(screen.getByRole('button', { name: '开始制作' })).toBeVisible();
  expect(screen.getByLabelText('你的故事', { exact: false })).toHaveValue(input.story_description);
  fireEvent.change(screen.getByLabelText('界面语言 / Interface language'), {
    target: { value: 'en' },
  });
  expect(screen.getByRole('button', { name: 'Start Film' })).toBeVisible();
  expect(localStorage.getItem('studio:v1:language')).toBe('"en"');
  expect(document.documentElement.lang).toBe('en');
  expect(toInput({ story_description: input.story_description, output_language: 'en' })).toEqual(
    input,
  );
  act(() => setLanguage('zh'));
  expect(document.documentElement.lang).toBe('zh-CN');
  expect(screen.getByRole('button', { name: '开始制作' })).toBeVisible();
});
it('localizes workflow vocabulary without changing unknown authored content', () => {
  setLanguage('zh');
  expect(t('waiting_human')).toBe('等待人工审核');
  expect(t('Creative Producer')).toBe('创意制片人');
  expect(t('An original user story')).toBe('An original user story');
});
