/**
 * Скрипт для Google Таблицы «Обновленный HR».
 * Отдаёт программе поздравлений ТОЛЬКО именинников на нужные даты
 * (ФИО и телефон), а не всю таблицу. Без секретного пароля ничего не отдаёт.
 */

// ▼ Вставьте между кавычками ссылку на таблицу «Обновленный HR» (из адресной строки браузера)
const TABLE_URL = 'ВСТАВЬТЕ_СЮДА_ССЫЛКУ_НА_ТАБЛИЦУ';

const SECRET = 'ВСТАВЬТЕ_СЮДА_ПАРОЛЬ_ИЗ_CONFIG_JSON';  // пароль, такой же как в config.json
const SHEET_NAME = 'Уволенные';                      // название листа

function doGet(e) {
  const p = (e && e.parameter) || {};
  if (p.token !== SECRET) return json_({ error: 'forbidden' });

  // даты приходят в виде "09-30,10-01" (месяц-день)
  const wanted = String(p.dates || '').split(',').filter(String);
  return json_({ people: findBirthdays_(wanted) });
}

function findBirthdays_(wanted) {
  const ss = SpreadsheetApp.openByUrl(TABLE_URL);
  const sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) throw new Error('Не найден лист «' + SHEET_NAME + '»');

  const rows = sheet.getDataRange().getValues();
  const header = rows[0].map(h => String(h).replace(/\s+/g, ' ').trim().toLowerCase());
  const col = start => header.findIndex(h => h.indexOf(start) === 0);

  const iFio = col('фио');
  const iBirth = col('дата рож');
  const iPhone = col('телефон');
  const iCall = col('имя для поздравления');  // необязательная колонка
  if (iFio < 0 || iBirth < 0 || iPhone < 0) {
    throw new Error('Не найдены колонки «ФИО», «Дата рождения» или «Телефон»');
  }

  const tz = ss.getSpreadsheetTimeZone();
  const people = [];
  for (let r = 1; r < rows.length; r++) {
    const row = rows[r];
    const md = monthDay_(row[iBirth], tz);
    if (!md || wanted.indexOf(md) === -1) continue;
    people.push({
      row: r + 1,
      fio: String(row[iFio]).replace(/\s+/g, ' ').trim(),
      phone: String(row[iPhone]).trim(),
      birthday: md,
      call: iCall >= 0 ? String(row[iCall]).trim() : ''
    });
  }
  return people;
}

// Дата рождения → "ММ-ДД". Понимает и настоящие даты, и текст "10.02.1974".
function monthDay_(v, tz) {
  if (v instanceof Date) return Utilities.formatDate(v, tz, 'MM-dd');
  const m = String(v).trim().match(/^(\d{1,2})\.(\d{1,2})/);
  if (!m) return null;
  return ('0' + m[2]).slice(-2) + '-' + ('0' + m[1]).slice(-2);
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

// Для проверки: выберите эту функцию вверху и нажмите «Выполнить».
// В «Журнале выполнения» появятся именинники на ближайшие 30 дней.
function proverka() {
  const dates = [];
  const tz = SpreadsheetApp.openByUrl(TABLE_URL).getSpreadsheetTimeZone();
  for (let i = 0; i < 30; i++) {
    dates.push(Utilities.formatDate(new Date(Date.now() + i * 86400000), tz, 'MM-dd'));
  }
  const people = findBirthdays_(dates);
  Logger.log('Именинников в ближайшие 30 дней: ' + people.length);
  people.forEach(p => Logger.log(p.birthday + '  ' + p.fio + '  ' + p.phone));
}
