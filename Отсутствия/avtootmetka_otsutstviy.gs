/**
 * Автоотметка отсутствий: Google таблица «Отпуска» + «График отсутствий» Битрикс24.
 *
 * Битрикс24 (робот «Исходящий вебхук» на стадии «Утверждено» процессов «Отпуск»,
 * «Больничный», «Административный отгул») вызывает эту программу по ссылке вида:
 *   https://script.google.com/macros/s/.../exec?key=СЕКРЕТ&fio=Фамилия Имя&start=01.06.2027&end=14.06.2027&vid=Отпуск ежегодный&uid=user_123
 *
 * Программа:
 *  1) проверяет секретный ключ (чтобы чужие не могли писать);
 *  2) только для ежегодного отпуска пишет «01.06-14.06(14)» в таблицу «Отпуска»
 *     (строка сотрудника по фамилии и имени, месяц начала, колонка по неделе);
 *  3) для любого вида (если пришёл uid) добавляет запись в список Битрикса
 *     «Автоотметка отсутствий», а процесс этого списка ставит отметку в график отсутствий;
 *  4) если в таблице стоит предварительная дата этого отпуска: совпала — пропускает,
 *     не совпала — стирает пересекающуюся (или ближайшую) предварительную запись года
 *     и пишет правильную; записи, уже поставленные по заявлениям, не трогает;
 *  5) при любой проблеме присылает письмо владельцу скрипта.
 *
 * Свойства скрипта (Настройки проекта → Свойства скрипта):
 *   KEY          — секрет для ссылки;
 *   BITRIX_HOOK  — входящий вебхук Битрикса с правом «Списки», вида https://b24.skzicard.ru/rest/1/abc.../
 */

var LIST_NAME = 'Плановые и фактические отпуска обновленная версия';
var STOP_WORDS = ['декрет', 'уволенные']; // ниже этих строк сотрудников не ищем
// Список «Автоотметка отсутствий» в Битриксе и его поля
var B24_LIST_ID = 433;
var B24_FIELD = {start: 'PROPERTY_2247', end: 'PROPERTY_2248', type: 'PROPERTY_2249', user: 'PROPERTY_2250'};

var MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
              'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

function doGet(e) { return handle_(e); }
function doPost(e) { return handle_(e); }

function handle_(e) {
  var p = (e && e.parameter) || {};
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    var key = PropertiesService.getScriptProperties().getProperty('KEY');
    if (!key || p.key !== key) return reply_('Неверный ключ');

    var vid = String(p.vid || '').toLowerCase();
    var results = [];

    if (vid.indexOf('ежегод') !== -1) {
      var t = mark_(String(p.fio || ''), String(p.start || ''), String(p.end || ''));
      if (t.indexOf('Записано') !== 0 && t.indexOf('Уже есть') !== 0) notify_('Таблица: ' + t, p);
      results.push('Таблица: ' + t);
    } else {
      results.push('Таблица: пропущено (' + (p.vid || 'вид не указан') + ')');
    }

    if (p.uid) {
      var g = addToChart_(String(p.uid), String(p.start || ''), String(p.end || ''), String(p.vid || 'Отсутствие'));
      if (g.indexOf('Добавлено') !== 0 && g.indexOf('Уже есть') !== 0) notify_('График: ' + g, p);
      results.push('График: ' + g);
    }

    var result = results.join('\n');
    return reply_(result);
  } catch (err) {
    notify_('Ошибка программы: ' + err, p);
    return reply_('Ошибка: ' + err);
  } finally {
    lock.releaseLock();
  }
}

function mark_(fio, startText, endText) {
  var start = parseDate_(startText);
  var end = parseDate_(endText);
  if (!fio.trim()) return 'Не пришло ФИО сотрудника';
  if (!start || !end) return 'Не понял даты: «' + startText + '» – «' + endText + '»';
  if (end < start) return 'Дата окончания раньше даты начала';

  var sheet = SpreadsheetApp.getActive().getSheetByName(LIST_NAME);
  if (!sheet) return 'Не найден лист «' + LIST_NAME + '»';

  var row = findRow_(sheet, fio);
  if (typeof row === 'string') return row;

  var monthTitle = MONTHS[start.getMonth()] + ' ' + start.getFullYear();
  var lastCol = sheet.getLastColumn();
  var header = sheet.getRange(1, 1, 1, lastCol).getDisplayValues()[0];
  var monthCol = -1;
  for (var c = 0; c < header.length; c++) {
    if (header[c].trim() === monthTitle) monthCol = c + 1; // берём последний такой столбец
  }
  if (monthCol === -1) return 'В таблице нет столбца «' + monthTitle + '»';

  var days = vacationDays_(start, end);
  var text = days === 1
    ? fmt_(start) + '(1)'
    : fmt_(start) + '-' + fmt_(end) + '(' + days + ')';
  var who = sheet.getRange(row, 1).getDisplayValue();

  // Что уже стоит у сотрудника: предварительный график и прошлые заявления
  var rowValues = sheet.getRange(row, 1, 1, lastCol).getDisplayValues()[0];
  var entries = readEntries_(header, rowValues);
  var done = loadDone_(start.getFullYear());
  var person = normalize_(who).trim();

  for (var i = 0; i < entries.length; i++) {
    if (sameDay_(entries[i].start, start) && sameDay_(entries[i].end, end)) {
      saveDone_(start.getFullYear(), done, person, fmt_(start));
      return 'Уже есть: ' + entries[i].raw.trim() + ' (строка ' + row + ')';
    }
  }

  // Предварительные записи того же года: ещё не начались и не подтверждены заявлениями
  var today = new Date(); today.setHours(0, 0, 0, 0);
  var draft = entries.filter(function (x) {
    return x.start.getFullYear() === start.getFullYear() && x.start >= today &&
      done.indexOf(person + '|' + fmt_(x.start)) === -1;
  });
  var remove = draft.filter(function (x) { return x.start <= end && x.end >= start; });
  if (remove.length === 0 && draft.length > 0) {
    draft.sort(function (x, y) { return Math.abs(x.start - start) - Math.abs(y.start - start); });
    remove = [draft[0]];
  }
  remove.forEach(function (x) { sheet.getRange(row, x.col).clearContent(); });
  var replaced = remove.map(function (x) { return x.raw.trim(); });

  var block = sheet.getRange(row, monthCol, 1, 4);
  var values = block.getDisplayValues()[0];
  var week = Math.min(3, Math.floor((start.getDate() - 1) / 7)); // 1–7 → 0, 8–14 → 1, 15–21 → 2, 22+ → 3
  for (var k = 0; k < 4; k++) {
    var idx = (week + k) % 4;
    if (!values[idx].trim()) {
      sheet.getRange(row, monthCol + idx).setValue(text);
      saveDone_(start.getFullYear(), done, person, fmt_(start));
      return 'Записано: ' + text + ' — ' + who +
        (replaced.length ? ' (вместо предварительной ' + replaced.join(', ') + ')' : '');
    }
  }
  // Не влезло: возвращаем стёртое на место
  remove.forEach(function (x) { sheet.getRange(row, x.col).setValue(x.raw); });
  return 'Все 4 ячейки месяца «' + monthTitle + '» у сотрудника заняты, не записал: ' + text;
}

// Разбирает записи строки сотрудника: «31.05-13.06.(14)», «с 05.04- 18.04(14)», «28.10(1)», «12.07.27-25.7(14)»…
function readEntries_(header, rowValues) {
  var out = [];
  var hMonth = -1, hYear = 0;
  for (var c = 1; c < rowValues.length; c++) {
    var h = String(header[c] || '').trim().split(/\s+/);
    if (h.length === 2 && MONTHS.indexOf(h[0]) !== -1) { hMonth = MONTHS.indexOf(h[0]); hYear = +h[1]; }
    var raw = rowValues[c];
    if (!raw || !raw.trim() || hMonth === -1) continue;
    var e = parseEntry_(raw, hMonth, hYear);
    if (e) { e.col = c + 1; e.raw = raw; out.push(e); }
  }
  return out;
}

function parseEntry_(raw, hMonth, hYear) {
  var pairs = [], re = /(\d{1,2})\.(\d{1,2})/g, m;
  while ((m = re.exec(raw)) !== null) {
    var d = +m[1], mo = +m[2];
    if (d >= 1 && d <= 31 && mo >= 1 && mo <= 12) pairs.push([d, mo - 1]);
  }
  if (!pairs.length) return null;
  var n = raw.match(/\((\d+)\)/);
  n = n ? +n[1] : 0;
  var y = hYear;
  if (pairs[0][1] - hMonth > 6) y--;
  if (hMonth - pairs[0][1] > 6) y++;
  var start = new Date(y, pairs[0][1], pairs[0][0]);
  var end = null;
  if (pairs.length > 1) {
    end = new Date(y, pairs[1][1], pairs[1][0]);
    if (pairs[0][1] - pairs[1][1] > 6) end.setFullYear(y + 1); // 28.12-10.01
  }
  if (!end || end < start) {
    end = new Date(start);
    if (n > 1) end.setDate(end.getDate() + n - 1);
  }
  return {start: start, end: end};
}

function sameDay_(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

// Записи, поставленные по заявлениям (их не стираем как предварительные)
function loadDone_(year) {
  var v = PropertiesService.getScriptProperties().getProperty('DONE_' + year);
  return v ? JSON.parse(v) : [];
}

function saveDone_(year, done, person, startDm) {
  var k = person + '|' + startDm;
  if (done.indexOf(k) !== -1) return;
  done.push(k);
  PropertiesService.getScriptProperties().setProperty('DONE_' + year, JSON.stringify(done));
}

// Отпускные дни: календарные дни без нерабочих праздников (ст. 120 и 112 ТК РФ)
var HOLIDAYS = ['01.01', '02.01', '03.01', '04.01', '05.01', '06.01', '07.01', '08.01',
                '23.02', '08.03', '01.05', '09.05', '12.06', '04.11'];

function vacationDays_(start, end) {
  var days = 0;
  for (var d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
    if (HOLIDAYS.indexOf(fmt_(d)) === -1) days++;
  }
  return days;
}

function findRow_(sheet, fio) {
  var want = words_(fio).slice(0, 2); // фамилия и имя (отчество не нужно)
  if (want.length < 2) return 'В ФИО меньше двух слов: «' + fio + '»';

  var names = sheet.getRange(1, 1, sheet.getLastRow(), 1).getDisplayValues();
  var found = [];
  for (var r = 1; r < names.length; r++) {
    var cell = names[r][0];
    var low = normalize_(cell).trim();
    if (STOP_WORDS.indexOf(low) !== -1) break;
    var have = words_(cell); // цифры (даты приёма) отбрасываются, девичьи фамилии в скобках остаются
    if (want.every(function (w) { return have.indexOf(w) !== -1; })) found.push(r + 1);
  }
  if (found.length === 0) return 'Сотрудник «' + fio + '» не найден в таблице';
  if (found.length > 1) return 'Сотрудник «' + fio + '» найден несколько раз (строки ' + found.join(', ') + ')';
  return found[0];
}

function words_(s) {
  return normalize_(s).replace(/[^а-яa-z\s-]/g, ' ').split(/\s+/).filter(String);
}

function normalize_(s) {
  return String(s).toLowerCase().replace(/ё/g, 'е');
}

// Понимает «01.06.2027», «01.06.2027 00:00:00» и «2027-06-01…»
function parseDate_(s) {
  s = String(s).trim();
  var m = s.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})/);
  if (m) return new Date(+m[3], +m[2] - 1, +m[1]);
  m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return new Date(+m[1], +m[2] - 1, +m[3]);
  return null;
}

function fmt_(d) {
  return ('0' + d.getDate()).slice(-2) + '.' + ('0' + (d.getMonth() + 1)).slice(-2);
}

// Добавляет запись в список «Автоотметка отсутствий»; график ставит процесс списка.
function addToChart_(uid, startText, endText, title) {
  var hook = PropertiesService.getScriptProperties().getProperty('BITRIX_HOOK');
  if (!hook) return 'В свойствах скрипта нет BITRIX_HOOK';
  if (hook.slice(-1) !== '/') hook += '/';

  var m = uid.match(/(\d+)/);
  if (!m) return 'Не понял сотрудника: «' + uid + '»';
  var user = 'user_' + m[1];

  var start = parseDate_(startText);
  var end = parseDate_(endText);
  if (!start || !end) return 'Не понял даты: «' + startText + '» – «' + endText + '»';
  var s = fmt_(start) + '.' + start.getFullYear();
  var e = fmt_(end) + '.' + end.getFullYear();

  var payload = {
    IBLOCK_TYPE_ID: 'lists',
    IBLOCK_ID: B24_LIST_ID,
    ELEMENT_CODE: 'abs_' + m[1] + '_' + s + '_' + e,
    'FIELDS[NAME]': title
  };
  payload['FIELDS[' + B24_FIELD.user + ']'] = user;
  payload['FIELDS[' + B24_FIELD.start + ']'] = s;
  payload['FIELDS[' + B24_FIELD.end + ']'] = e;
  payload['FIELDS[' + B24_FIELD.type + ']'] = title;

  var resp = UrlFetchApp.fetch(hook + 'lists.element.add.json', {
    method: 'post', payload: payload, muteHttpExceptions: true
  });
  var body = resp.getContentText();
  var data;
  try { data = JSON.parse(body); } catch (err) { return 'Битрикс ответил непонятно: ' + body.slice(0, 200); }
  if (data.result) return 'Добавлено: ' + title + ' ' + s + '–' + e;
  var msg = String(data.error_description || data.error || body).slice(0, 300);
  if (/already exists|уже существ/i.test(msg) || (/код|code/i.test(msg) && /уже|exist|уникал/i.test(msg))) return 'Уже есть: ' + s + '–' + e;
  return 'Битрикс не принял запись: ' + msg;
}

function notify_(text, p) {
  var to = Session.getEffectiveUser().getEmail();
  if (!to) return;
  MailApp.sendEmail(to, 'Отсутствия: нужна ручная отметка',
    text + '\n\nДанные из Битрикса:\nФИО: ' + (p.fio || '') +
    '\nС: ' + (p.start || '') + '\nПо: ' + (p.end || '') + '\nВид: ' + (p.vid || '') +
    '\nКод сотрудника: ' + (p.uid || ''));
}

function reply_(text) {
  return ContentService.createTextOutput(text);
}

// Для проверки из редактора: выберите эту функцию и нажмите «Выполнить».
// Ничего не пишет в таблицу, только показывает в журнале, какую строку нашёл.
function proverka() {
  var sheet = SpreadsheetApp.getActive().getSheetByName(LIST_NAME);
  Logger.log(findRow_(sheet, 'Иванова Анна'));
}
