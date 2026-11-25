/* Мелкие удобства поверх серверной вёрстки.
 *
 * Ни один экран не зависит от этого файла: страницы рисует сервер, формы
 * отправляются обычным POST, переключатель срока — это ссылки. Здесь только
 * то, что без скриптов сделать нельзя, — буфер обмена и ввод кода.
 *
 * Политика портала (`script-src 'self'`) не допускает ни встроенных
 * скриптов, ни обработчиков в атрибутах: всё вешается отсюда по data-атрибуту.
 */

(() => {
  'use strict';

  /* Кнопка «Скопировать» рядом с полем: data-copy="<id поля>". */
  const copyLabelTimeout = 1600;

  document.querySelectorAll('[data-copy]').forEach((button) => {
    const field = document.getElementById(button.dataset.copy);
    if (!(field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement)) {
      return;
    }

    const original = button.textContent;

    button.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(field.value);
      } catch {
        // Буфер может быть закрыт настройками браузера или http-origin:
        // выделяем текст, чтобы человек скопировал сам.
        field.focus();
        field.select();
        return;
      }

      button.textContent = 'Скопировано';
      window.setTimeout(() => {
        button.textContent = original;
      }, copyLabelTimeout);
    });
  });

  /* Поле кода из письма: только цифры, отправка по заполнении. */
  const code = document.querySelector('[data-code-input]');
  if (code instanceof HTMLInputElement) {
    const expected = Number(code.maxLength) || 6;

    code.addEventListener('input', () => {
      const digits = code.value.replace(/\D+/g, '').slice(0, expected);
      if (digits !== code.value) {
        code.value = digits;
      }
      if (digits.length === expected) {
        code.form?.requestSubmit();
      }
    });
  }

  /* Формы, которые что-то списывают: защита от второго нажатия.
     Кнопку отключаем после отправки — но только когда форма реально уходит,
     иначе неудачная проверка браузером оставила бы её мёртвой. */
  document.querySelectorAll('form[data-once]').forEach((form) => {
    form.addEventListener('submit', () => {
      if (!form.checkValidity()) {
        return;
      }
      form.querySelectorAll('button[type="submit"], button:not([type])').forEach((button) => {
        // disabled снял бы кнопку с отправки формы; достаточно запретить клики.
        button.setAttribute('aria-disabled', 'true');
      });
    });
  });
})();
