/**
 * Отпуска → профиль сотрудника в Битриксе.
 * Берёт из таблицы «ОТПУСКА в ПРОФИЛЕ» (Лист1) email и оставшиеся дни
 * и записывает дни в поле профиля сотрудника в Битриксе.
 *
 * Порядок настройки:
 *   1) вставьте вебхук в WEBHOOK (строка ниже) и сохраните (Cmd + S);
 *   2) запустите obnovitOtpuska — проверка вручную;
 *   3) запустите vklyuchitAvtozapusk — дальше всё будет само
 *      в первый рабочий день каждого месяца в 8–9 утра.
 */

// ▼ Вставьте между кавычками ссылку входящего вебхука Битрикса (заканчивается на /)
const WEBHOOK = 'ВСТАВЬТЕ_СЮДА_ВЕБХУК';

// ▼ Код поля в профиле, куда писать дни (на портале это поле «LinkedIn»)
const FIELD = 'UF_LINKEDIN';

const TABLE_URL = 'ВСТАВЬТЕ_СЮДА_ССЫЛКУ_НА_ТАБЛИЦУ';
const SHEET_NAME = 'Лист1';

// Праздники, которые бывают в начале месяца (ММ-ДД)
const PRAZDNIKI = ['01-01','01-02','01-03','01-04','01-05','01-06','01-07','01-08','05-01','05-09'];
// Переносы выходных на конкретный год (ГГГГ-ММ-ДД) — дописывать раз в год, например '2027-05-03'
const PERENOSY = [];

// Главная функция: обновляет отпуска всех сотрудников из таблицы
function obnovitOtpuska() {
  if (FIELD.indexOf('UF_') !== 0) throw new Error('Не вписан код поля в строке FIELD (узнать — функцией naytiPole)');

  // Сначала проверяем, что Битрикс принимает вебхук
  try {
    bitrix_('user.current', {});
  } catch (e) {
    MailApp.sendEmail(Session.getEffectiveUser().getEmail(),
      'Отпуска в Битриксе: не обновилось — проблема с вебхуком',
      'Битрикс не принял вебхук: ' + e.message + '\n\n' +
      'Что сделать: создайте новый входящий вебхук в Битриксе (права «Пользователи (user)»), ' +
      'вставьте его в строку WEBHOOK скрипта «Отпуска в Битрикс» и запустите obnovitOtpuska вручную.');
    throw e;
  }

  const sheet = SpreadsheetApp.openByUrl(TABLE_URL).getSheetByName(SHEET_NAME);
  if (!sheet) throw new Error('Не найден лист «' + SHEET_NAME + '»');
  const rows = sheet.getDataRange().getValues().slice(1);  // без строки заголовков

  let ok = 0;
  const problems = [];
  rows.forEach(function (row) {
    const email = String(row[0]).trim().toLowerCase();
    const days = row[1];
    if (!email) return;
    try {
      const users = bitrix_('user.get', { 'FILTER[EMAIL]': email });
      if (!users.length) {
        problems.push(email + ' — не найден в Битриксе');
        return;
      }
      const params = { ID: users[0].ID };
      params[FIELD] = days;
      bitrix_('user.update', params);
      ok++;
    } catch (e) {
      problems.push(email + ' — ' + e.message);
    }
    Utilities.sleep(300);  // Битрикс не любит слишком частые запросы
  });

  Logger.log('Обновлено: ' + ok);
  problems.forEach(function (p) { Logger.log('Не получилось: ' + p); });
  if (problems.length) {
    MailApp.sendEmail(Session.getEffectiveUser().getEmail(),
      'Отпуска в Битриксе: не всё обновилось',
      'Обновлено: ' + ok + '\n\nНе получилось:\n' + problems.join('\n'));
  }
}

// Запускается каждый день, но работает только в первый рабочий день месяца
function zapuskVPervyyRabochiyDen() {
  if (!pervyyRabochiyDen_(new Date())) return;
  obnovitOtpuska();
}

function rabochiyDen_(d) {
  const mmdd = Utilities.formatDate(d, 'Europe/Moscow', 'MM-dd');
  const polnaya = Utilities.formatDate(d, 'Europe/Moscow', 'yyyy-MM-dd');
  const den = Number(Utilities.formatDate(d, 'Europe/Moscow', 'u'));  // 1 = пн, 7 = вс
  return den <= 5 && PRAZDNIKI.indexOf(mmdd) === -1 && PERENOSY.indexOf(polnaya) === -1;
}

function pervyyRabochiyDen_(segodnya) {
  if (!rabochiyDen_(segodnya)) return false;
  const d = new Date(segodnya.getFullYear(), segodnya.getMonth(), 1, 12);
  while (d.getDate() < segodnya.getDate()) {
    if (rabochiyDen_(d)) return false;  // рабочий день в этом месяце уже был
    d.setDate(d.getDate() + 1);
  }
  return true;
}

// Показывает в журнале дополнительные поля профиля — чтобы найти код поля с отпуском
function naytiPole() {
  const fields = bitrix_('user.userfield.list', {});
  fields.forEach(function (f) {
    const label = (f.EDIT_FORM_LABEL && (f.EDIT_FORM_LABEL.ru || f.EDIT_FORM_LABEL.en)) || '';
    Logger.log(f.FIELD_NAME + '   ' + label);
  });
  if (!fields.length) Logger.log('Дополнительных полей не найдено');
}

// Включает автозапуск в первый рабочий день месяца в 8–9 утра (запустить один раз)
function vklyuchitAvtozapusk() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    const f = t.getHandlerFunction();
    if (f === 'obnovitOtpuska' || f === 'zapuskVPervyyRabochiyDen') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('zapuskVPervyyRabochiyDen').timeBased().everyDays(1).atHour(8).create();
  Logger.log('Готово: обновление будет запускаться само в первый рабочий день каждого месяца в 8–9 утра');
}

function bitrix_(method, params) {
  if (WEBHOOK.indexOf('http') !== 0) throw new Error('Не вставлен вебхук в строке WEBHOOK');
  const query = Object.keys(params).map(function (k) {
    return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]);
  }).join('&');
  const url = WEBHOOK.replace(/\/?$/, '/') + method + '.json' + (query ? '?' + query : '');
  const resp = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  const data = JSON.parse(resp.getContentText());
  if (data.error) throw new Error(data.error_description || data.error);
  return data.result;
}
