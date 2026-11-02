// Проверка вебхука: только чтение, ничего не записывает.
function proverkaVebhuka() {
  var hook = PropertiesService.getScriptProperties().getProperty('BITRIX_HOOK');
  if (!hook) { Logger.log('Нет BITRIX_HOOK в свойствах скрипта'); return; }
  if (hook.slice(-1) !== '/') hook += '/';
  var resp = UrlFetchApp.fetch(hook + 'lists.get.json', {
    method: 'post',
    payload: { IBLOCK_TYPE_ID: 'lists', IBLOCK_ID: B24_LIST_ID },
    muteHttpExceptions: true
  });
  var data = JSON.parse(resp.getContentText());
  if (data.result) Logger.log('✅ Вебхук работает, список 433 доступен');
  else Logger.log('❌ Ошибка: ' + (data.error_description || data.error));
}
