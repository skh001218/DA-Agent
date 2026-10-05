'use strict';
// Locally served CodeMirror 5.65.21; the original textarea remains the fallback.
(() => {
  const textarea = document.getElementById('sql');
  if (!textarea || !window.CodeMirror) return;
  let editor;
  try {
    editor = CodeMirror.fromTextArea(textarea, {
      mode: 'text/x-pgsql', lineNumbers: true, lineWrapping: true,
      indentUnit: 2, tabSize: 2, viewportMargin: 10,
      screenReaderLabel: 'SQL 입력',
      extraKeys: { Tab: false, 'Shift-Tab': false }
    });
    editor.on('change', () => editor.save());
    const label = document.querySelector('label[for="sql"]');
    if (label) label.addEventListener('click', event => { event.preventDefault(); editor.focus(); });
    window.sqlEditor = {
      getValue: () => editor.getValue(),
      reset: () => { editor.setValue(''); editor.clearHistory(); editor.save(); },
      refresh: () => editor.refresh()
    };
  } catch (error) {
    if (editor) editor.toTextArea();
    textarea.hidden = false;
    textarea.style.display = '';
    console.warn('SQL 편집기 대신 기본 입력을 사용합니다.', error);
  }
})();
