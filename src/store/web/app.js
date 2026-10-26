'use strict';

const $ = id => document.getElementById(id);
const state = { card: null, event: null, profile: null, runtime: {}, session: {}, events: [], cards: new Map(), version: 0 };
const FACTORS = { RATE: 'Ставка', FX: 'Валютный курс', INFL: 'Инфляция', ENERGY: 'Энергетика', COMMOD: 'Сырьё', DEMAND: 'Спрос', LABOR: 'Рынок труда', CREDIT: 'Кредитование', BUDGET: 'Бюджет', REG: 'Регулирование', GEOPOL: 'Геополитика', CPTY: 'Контрагенты' };
const INDUSTRIES = { 'Construction of residential buildings': 'Строительство', 'Steel production': 'Металлургия', 'Mining of precious-metal ores': 'Добыча металлов', 'Retail trade': 'Розничная торговля', 'Growing of cereals': 'Сельское хозяйство', 'Freight road transport': 'Логистика', 'Computer programming': 'IT и технологии', 'Manufacture of motor vehicles': 'Автопроизводство' };
const PRODUCT_NAMES = { loa: 'Кредитование', lea: 'Лизинг', dep: 'Депозиты', rko: 'Расчётный счёт', gar: 'Гарантии', zpp: 'Зарплатный проект', factoring: 'Факторинг' };
const FEATURE_NAMES = { C_PRODUCTS: 'Продукты компании', C_SECTOR: 'Отраслевой профиль', C_TYPE: 'Тип клиента', C_COMPLETE: 'Полнота данных', C_NET: 'Чистая процентная позиция', C_LIMITS: 'Ограничения данных' };
let noticeTimer;

function el(tag, text, parent, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  if (parent) parent.append(node);
  return node;
}
function icon(name, parent) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', '/assets/icons.svg#' + name);
  svg.append(use);
  if (parent) parent.append(svg);
  return svg;
}
function button(text, parent, className = 'secondary', action) {
  const node = el('button', text, parent, className);
  node.type = 'button';
  if (action) node.onclick = action;
  return node;
}
function notice(text = '', error = false, persistent = false) {
  clearTimeout(noticeTimer);
  $('notice').textContent = text;
  $('notice').className = error ? 'error' : '';
  if (text && !persistent) noticeTimer = setTimeout(() => notice(), error ? 8500 : 4500);
}
function dateText(value, includeYear = false) {
  if (!value) return 'Дата не указана';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'Дата не указана';
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', ...(includeYear ? { year: 'numeric' } : {}) }).format(parsed);
}
function industry(text) { return INDUSTRIES[text] || text || 'Компания'; }
function textOf(item) { return typeof item === 'string' ? item : item?.text || item?.claim || item?.reason || ''; }
function valueText(value) { return value === null || value === undefined ? 'Нет данных' : typeof value === 'object' ? JSON.stringify(value) : String(value); }
function safeLink(parent, text, url) {
  if (!url) return null;
  try {
    const target = new URL(url, location.origin);
    if (!['http:', 'https:'].includes(target.protocol)) return null;
    const a = el('a', text, parent);
    a.href = target.href; a.target = '_blank'; a.rel = 'noopener noreferrer';
    icon('arrow', a);
    return a;
  } catch { return null; }
}
async function api(path, body, method) {
  let response;
  try {
    response = await fetch(path, { method: method || (body ? 'POST' : 'GET'), headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
  } catch { throw Error('Не удалось связаться с сервисом. Проверьте соединение и повторите запрос.'); }
  let data;
  try { data = await response.json(); } catch { throw Error('Сервис временно недоступен. Попробуйте позже.'); }
  if (!response.ok) {
    if (response.status === 401) showLogin();
    let message = 'Не удалось выполнить запрос. Проверьте данные и попробуйте снова.';
    const detail = typeof data.detail === 'string' ? data.detail : '';
    if (response.status === 401) message = 'Войдите в сервис с действующим кодом доступа.';
    if (response.status === 403) message = 'Для этого действия нет доступа. Ссылка на новость также должна быть из разрешённого источника.';
    if (response.status === 409) {
      const code = data.detail?.code;
      message = code === 'model_not_configured' ? 'Модели не подключены. Администратору нужно настроить API. Новость доступна для просмотра.'
        : code === 'preparation_failed' ? 'Не удалось обработать новость: запрос к моделям не завершился. Попробуйте позже.'
        : 'Разбор не прошёл проверку. Непроверенные выводы не показаны. Попробуйте ещё раз позже.';
    }
    if (response.status === 404) message = 'Событие или карточка недоступны. Обновите ленту.';
    if (detail.includes('PE_PROFILE_NOT_READY')) message = 'Для компании ещё не загружены актуальные расчёты. Администратору нужно обновить агрегаты.';
    if (detail.includes('PE_PROFILE_AMBIGUOUS')) message = 'В данных компании найден конфликт. Нужна проверка администратора.';
    if (detail.includes('EVENT_NO_SUPPORTED_FACTS')) message = 'В тексте не удалось выделить подтверждённое экономическое событие. Добавьте более полный источник.';
    throw Error(message);
  }
  return data;
}
function showLogin() {
  $('login').hidden = false; $('workspace').hidden = true; $('logout').hidden = true;
  state.card = null; state.event = null; state.cards.clear(); state.version++;
  for (const dialog of document.querySelectorAll('dialog[open]')) dialog.close();
}
function openDialog(id) { const dialog = $(id); if (!dialog.open) dialog.showModal(); }
for (const closer of document.querySelectorAll('[data-close]')) closer.onclick = () => closer.closest('dialog').close();
for (const dialog of document.querySelectorAll('dialog')) dialog.addEventListener('click', event => {
  if (event.target !== dialog) return;
  const r = dialog.getBoundingClientRect();
  if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close();
});

async function login(token) {
  const buttons = [...$('login').querySelectorAll('button')];
  buttons.forEach(b => { b.disabled = true; });
  try { await api('/session', { token }); $('token').value = ''; await start(); }
  finally { buttons.forEach(b => { b.disabled = false; }); }
}
$('login-form').onsubmit = async event => { event.preventDefault(); try { await login($('token').value); } catch (e) { notice(e.message, true); } };
$('logout').onclick = async () => { try { await api('/session', null, 'DELETE'); showLogin(); notice(); } catch (e) { notice(e.message, true); } };
async function start() {
  state.session = await api('/session');
  const [clients, personas, runtime] = await Promise.all([api('/clients'), api('/personas'), api('/runtime')]);
  state.runtime = runtime;
  $('company').replaceChildren();
  for (const company of clients) el('option', company.pseudonym || 'Компания', $('company')).value = company.client_id;
  $('persona').replaceChildren();
  for (const role of personas) {
    const option = el('option', role.name, $('persona')); option.value = role.persona; option.title = role.jtbd;
  }
  $('persona').value = 'PER-03';
  $('today').textContent = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', weekday: 'long' }).format(new Date()).toLocaleUpperCase('ru-RU');
  $('login').hidden = true; $('workspace').hidden = false; $('logout').hidden = false;
  $('admin-open').hidden = state.session.role !== 'admin';
  $('runtime-label').textContent = runtime.demo ? 'Демо-режим' : runtime.model_endpoint_configured ? 'Сервис подключён' : 'Генерация недоступна';
  $('runtime-dot').className = 'status-dot' + (runtime.model_endpoint_configured ? '' : ' offline');
  await refresh();
}
function cacheKey(docId) { return [$('company').value, $('persona').value, docId].join(':'); }
function showEmpty(message) {
  $('card').removeAttribute('aria-busy');
  state.card = null;
  const root = $('card'); root.replaceChildren();
  const empty = el('div', undefined, root, 'empty-state'); icon('grid', empty); el('p', message, empty);
  $('card-status').textContent = 'Нет выбранного события';
}
async function refresh() {
  state.cards.clear();
  const version = ++state.version;
  state.card = null; state.event = null;
  $('feed').replaceChildren(); $('feed-count').textContent = '…';
  showEmpty('Подбираем события для вашей компании…');
  $('board').dataset.mobile = 'feed';
  $('refresh').disabled = true;
  try {
    if (!$('company').value) throw Error('Нет доступных компаний. Администратору нужно загрузить данные.');
    const params = new URLSearchParams({ client_id: $('company').value, persona: $('persona').value });
    const [profile, feed] = await Promise.all([api('/clients/' + encodeURIComponent($('company').value) + '/profile'), api('/feed?' + params)]);
    if (version !== state.version) return;
    state.profile = profile;
    const option = $('company').selectedOptions[0];
    if (option) { option.textContent = industry(profile.mainokved_name); option.title = profile.mainokved_name || 'Компания'; }
    state.events = Array.isArray(feed) ? feed : feed.items || feed.events || [];
    $('feed-count').textContent = state.events.length;
    for (const event of state.events) {
      const node = button('', $('feed'), 'event', () => selectEvent(event, true));
      node.dataset.docId = event.doc_id; node.setAttribute('aria-pressed', 'false');
      const meta = el('div', undefined, node, 'event-meta');
      const factors = Object.entries(event.factors || {}).filter(([,v]) => v > 0).sort((a,b) => b[1] - a[1]);
      el('span', FACTORS[factors[0]?.[0]] || 'Экономика', meta, 'event-category');
      el('span', '·', meta); el('span', dateText(event.published_at_utc || event.published_at), meta);
      el('strong', event.title || 'Экономическое событие', node);
      const bottom = el('div', undefined, node, 'event-bottom');
      const relevance = typeof event.relevance === 'number' ? Math.round(event.relevance * 100) : null;
      const chip = el('span', relevance === null ? 'По профилю компании' : 'Релевантность ' + relevance + '/100', bottom, 'event-chip');
      chip.title = (event.provisional ? 'Предварительная оценка по лексическим факторам. ' : '') + 'Баллы соответствия профилю, не вероятность финансового результата.';
      icon('arrow', bottom);
    }
    if (state.events.length) selectEvent(state.events[0], false);
    else { el('p', 'Подходящих событий пока нет. Добавьте свою новость для разбора.', $('feed'), 'empty-state'); showEmpty('Добавьте новость — разберём её влияние на вашу компанию.'); }
  } catch (e) { if (version === state.version) { showEmpty(e.message); notice(e.message, true); $('feed-count').textContent = '0'; } }
  finally { if (version === state.version) $('refresh').disabled = false; }
}
$('refresh').onclick = refresh; $('company').onchange = refresh; $('persona').onchange = refresh;
$('home').onclick = () => { $('board').dataset.mobile = 'feed'; if (state.event) selectEvent(state.event, false); };
$('back-feed').onclick = () => { $('board').dataset.mobile = 'feed'; };
for (const id of ['add-news', 'add-news-rail']) $(id).onclick = () => openDialog('news-dialog');

function selectEvent(event, navigate) {
  $('card').removeAttribute('aria-busy');
  state.version++; state.event = event; state.card = null;
  // A selection change invalidates a pending feed/generation response.
  $('refresh').disabled = false;
  for (const node of $('feed').querySelectorAll('.event')) node.setAttribute('aria-pressed', String(node.dataset.docId === event.doc_id));
  if (navigate) $('board').dataset.mobile = 'reader';
  const cached = state.cards.get(cacheKey(event.doc_id));
  if (cached) { state.card = cached; renderCard(cached); } else renderPreview(event);
}
function setStatus(text, kind = '') { $('card-status').textContent = text; $('card-status').className = 'status-badge ' + kind; }
function readerShell(title, source, isCard) {
  const root = $('card'); root.replaceChildren();
  const heading = el('div', undefined, root, 'article-heading');
  el('h2', title || 'Разбор события', heading);
  const meta = el('div', undefined, heading, 'source-meta');
  icon('clock', meta); el('span', dateText(source.published_at_utc || source.published_at, true), meta);
  el('span', '·', meta); el('span', state.runtime.demo ? 'Демонстрационная подборка' : 'Публикация источника', meta);
  safeLink(meta, 'Источник', source.url);
  const tabs = el('div', undefined, root, 'reader-tabs'); tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', 'Разделы разбора');
  const panels = el('div', undefined, root, 'reader-panels');
  const result = {};
  for (const [key, title] of [['overview','Обзор'],['sources','Источники'],['scenario','Расчёты'],['trace','Как получен вывод']]) {
    const tab = button(title, tabs, '', () => activate(key)); tab.id = 'tab-' + key; tab.setAttribute('role', 'tab'); tab.setAttribute('aria-controls', 'panel-' + key);
    tab.disabled = !isCard && ['scenario', 'trace'].includes(key);
    const panel = el('section', undefined, panels, 'tab-panel'); panel.id = 'panel-' + key; panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', tab.id); panel.tabIndex = 0;
    result[key] = panel;
    tab.addEventListener('keydown', event => {
      const keys = ['ArrowRight','ArrowLeft','Home','End']; if (!keys.includes(event.key)) return;
      event.preventDefault(); const enabled = [...tabs.querySelectorAll('button:not(:disabled)')];
      let index = enabled.indexOf(tab);
      if (event.key === 'ArrowRight') index = (index + 1) % enabled.length;
      if (event.key === 'ArrowLeft') index = (index - 1 + enabled.length) % enabled.length;
      if (event.key === 'Home') index = 0; if (event.key === 'End') index = enabled.length - 1;
      enabled[index].click(); enabled[index].focus();
    });
  }
  function activate(key) {
    for (const panel of panels.children) panel.hidden = panel.id !== 'panel-' + key;
    for (const tab of tabs.children) { const active = tab.id === 'tab-' + key; tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1; }
    panels.scrollTop = 0;
    if (key === 'trace' && !result.trace.dataset.loaded) loadTrace(result.trace, state.card);
  }
  activate('overview');
  return result;
}
function list(parent, items) {
  const values = (items || []).map(textOf).filter(Boolean);
  if (!values.length) return null;
  const ul = el('ul', undefined, parent, 'claim-list'); for (const value of values) el('li', value, ul);
  return ul;
}
function layer(parent, title, items, label, className = '') {
  if (!(items || []).some(textOf)) return null;
  const box = el('section', undefined, parent, 'layer ' + className);
  if (label) el('div', label, box, 'layer-label');
  el('h3', title, box); list(box, items); return box;
}
function renderPreview(event, error) {
  setStatus(error ? 'Разбор недоступен' : 'Обзор события');
  const panels = readerShell(event.title, event, false);
  const content = el('div', undefined, panels.overview, 'preview-content');
  if (error) el('p', error, content, 'warning');
  layer(content, 'Что произошло', [event.snippet || 'Откройте первоисточник, чтобы прочитать полную публикацию.'], 'ИЗ ПУБЛИКАЦИИ', 'fact-block');
  const box = el('section', undefined, content, 'preview-cta'); icon('spark', box);
  el('h3', 'Что это значит для вашего бизнеса?', box);
  el('p', 'Свяжем событие с отраслью и продуктами компании, выделим возможные последствия и вопросы для проверки.', box);
  const steps = el('div', undefined, box, 'preview-steps');
  for (const text of ['Почему важно', 'Риски и возможности', 'Что проверить']) el('span', text, steps);
  const b = button(error ? 'Повторить разбор' : 'Разобрать влияние', box, 'primary', () => generate(event)); icon('arrow', b);
  el('p', 'AI-разбор запускается по вашему запросу. Первичная сортировка ленты предварительная.', content, 'small-note');
  if (!state.runtime.model_endpoint_configured) el('p', 'Генерация сейчас недоступна: требуется подключение моделей. Текст новости и источники доступны для просмотра.', content, 'small-note');
  renderSources(panels.sources, null, event);
}
async function generate(event) {
  const version = ++state.version;
  state.card = null; state.event = event; $('board').dataset.mobile = 'reader';
  for (const node of $('feed').querySelectorAll('.event')) node.setAttribute('aria-pressed', String(node.dataset.docId === event.doc_id));
  setStatus('Готовим разбор');
  const root = $('card'); root.replaceChildren(); root.setAttribute('aria-busy', 'true');
  const loading = el('div', undefined, root, 'loading-state'); icon('spark', loading);
  el('h2', 'Собираем картину события', loading, 'loading-title');
  el('p', 'Извлечём факты, учтём профиль компании и проверим выводы независимой моделью.', loading);
  for (let i = 0; i < 5; i++) el('div', undefined, loading, 'skeleton' + (i % 2 ? ' short' : ''));
  try {
    const card = await api('/cards/generate', { client_id: $('company').value, persona: $('persona').value, event_doc_id: event.doc_id });
    if (version !== state.version) return;
    state.card = card; state.cards.set(cacheKey(event.doc_id), card); renderCard(card); notice();
  } catch (e) { if (version === state.version) { renderPreview(event, e.message); notice('Непроверенный разбор не опубликован.', true); } }
  finally { if (version === state.version) root.removeAttribute('aria-busy'); }
}
function renderCard(card) {
  const source = card.layers?.fact || state.event || {};
  setStatus(card.demo ? 'Демонстрационный ответ' : card.publishable ? 'Проверено AI' : 'Проверка не завершена', card.demo ? 'demo' : card.publishable ? 'checked' : '');
  const panels = readerShell(source.title, source, true);
  if (card.partial) el('p', 'Часть утверждений исключена при проверке. Учитывайте ограничения разбора.', panels.overview, 'warning');
  const grid = el('div', undefined, panels.overview, 'overview-grid');
  const main = el('div', undefined, grid, 'overview-main');
  const side = el('div', undefined, grid, 'overview-side');
  layer(main, 'Что произошло', card.confirmed_facts, 'ФАКТ ИЗ ИСТОЧНИКА', 'fact-block');
  layer(main, 'Почему это касается вас', card.why_you, 'ДЛЯ ВАШЕЙ КОМПАНИИ', 'personal-block');
  const inference = card.model_inference || {};
  const ai = layer(main, 'Возможные последствия', inference.impact_chain, 'ВЫВОД AI', 'inference-label');
  if (!ai) el('p', 'Данных для обоснованной оценки последствий недостаточно.', main, 'muted');
  const impact = el('div', undefined, main, 'impact-columns');
  layer(impact, 'Риски', inference.risks, null, 'risk'); layer(impact, 'Возможности', inference.opportunities, null, 'opportunity');
  const bank = card.bank_view || {};
  const bankBox = el('section', undefined, side, 'bank-card'); el('span', 'А', bankBox, 'bank-mark'); el('h3', 'Позиция Альфа-Банка', bankBox);
  if (!bank.items?.length) el('p', 'Подтверждённая позиция по этому событию отсутствует. Вывод AI не отражает позицию банка.', bankBox);
  else for (const item of bank.items) {
    list(bankBox, [item]); el('div', dateText(item.as_of, true) + (item.stale ? ' · Устаревшая аналитика' : ''), bankBox, 'bank-date');
    if (item.doc_id && item.page) safeLink(bankBox, 'Оригинал · стр. ' + item.page, '/analytics/' + encodeURIComponent(item.doc_id) + '/pages/' + item.page);
  }
  if (inference.actions_or_questions?.length) {
    const actions = el('section', undefined, side, 'action-card'); el('h3', 'Что стоит проверить', actions); list(actions, inference.actions_or_questions);
  }
  if (card.offer?.shown && card.offer.text) layer(side, 'Предложение банка', [card.offer.text], 'БАНКОВСКИЙ ПРОДУКТ', 'action-card');
  const unknowns = [...new Set([...(card.unknowns || []), ...(inference.unknowns || [])].map(textOf).filter(Boolean))];
  if (unknowns.length) layer(main, 'Что пока неизвестно', unknowns, null);
  const confidence = typeof inference.confidence === 'number' ? inference.confidence.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) : 'нет оценки';
  el('p', 'Обоснованность AI: ' + confidence + '. Внутренняя оценка, не вероятность финансового результата.', main, 'small-note');
  if (card.demo) el('p', 'Демонстрационный ответ на синтетическом профиле. Не опубликован как рабочая проверенная карточка.', panels.overview, 'disclaimer');
  el('p', card.disclaimer || 'Не является индивидуальной инвестиционной рекомендацией.', panels.overview, 'disclaimer');
  renderSources(panels.sources, card, source); renderScenario(panels.scenario, card);
  renderConversation($('card'), card);
}
function renderSources(parent, card, event) {
  el('h3', 'На чём основан разбор', parent, 'section-heading');
  el('p', 'Публикация, материалы банка и их точные фрагменты.', parent, 'muted');
  const news = el('section', undefined, parent, 'source-card');
  el('div', 'ИСТОЧНИК НОВОСТИ', news, 'layer-label'); el('h3', event.title || 'Публикация', news);
  el('p', dateText(event.published_at, true), news);
  const quote = event.snippet || card?.layers?.fact?.sources?.[0]?.text;
  if (quote) el('blockquote', quote, news);
  if (!safeLink(news, 'Открыть публикацию', event.url)) el('p', state.runtime.demo ? 'Учебная публикация. Внешнего первоисточника нет.' : 'Внешняя ссылка не предоставлена. Новость загружена текстом.', news, 'muted');
  const items = card?.bank_view?.items || [];
  for (const item of items) {
    const box = el('section', undefined, parent, 'source-card'); el('div', 'ПОЗИЦИЯ БАНКА', box, 'layer-label');
    el('h3', item.doc_id + ' · страница ' + item.page, box); el('p', dateText(item.as_of, true) + (item.stale ? ' · Устаревший материал' : ''), box);
    el('blockquote', item.quote_span || item.claim, box);
    if (item.doc_id && item.page) safeLink(box, 'Открыть страницу PDF', '/analytics/' + encodeURIComponent(item.doc_id) + '/pages/' + item.page);
  }
  if (!items.length) el('p', 'Проверенные фрагменты аналитики банка для этого разбора не использованы.', parent, 'small-note');
}
function renderConversation(root, card) {
  const bottom = el('div', undefined, root, 'reader-bottom');
  const row = el('div', undefined, bottom, 'feedback-row'); el('span', 'Был ли полезен этот разбор?', row);
  const actions = el('div', undefined, row, 'feedback-actions');
  const useful = button('Полезно', actions, '', async () => {
    useful.disabled = true;
    try { await api('/cards/' + card.card_id + '/feedback', { helpful: true }); useful.setAttribute('aria-pressed', 'true'); notice('Спасибо. Оценка сохранена.'); }
    catch (e) { notice(e.message, true); } finally { useful.disabled = false; }
  }); useful.prepend(icon('like')); useful.setAttribute('aria-pressed', 'false');
  const unhelpful = button('Не помогло', actions, 'down', () => feedbackReason(card)); unhelpful.prepend(icon('like'));
  const history = el('details', undefined, bottom, 'chat-history'); history.hidden = true;
  const historyTitle = el('summary', 'Диалог по карточке', history);
  const messages = el('div', undefined, history, 'messages'); messages.setAttribute('role', 'log'); messages.setAttribute('aria-label', 'Диалог по карточке');
  const form = el('form', undefined, bottom, 'chat-form');
  const label = el('label', 'Вопрос по этой карточке', form, 'sr-only'); label.htmlFor = 'card-question';
  const input = el('input', undefined, form); input.id = 'card-question'; input.placeholder = 'Что это меняет для моей компании?'; input.required = true; input.maxLength = 4000; input.autocomplete = 'off';
  const send = el('button', undefined, form, 'send-button'); send.type = 'submit'; send.setAttribute('aria-label', 'Спросить'); icon('send', send);
  el('p', 'Задайте вопрос или попросите посмотреть на событие в другом разрезе', bottom, 'chat-caption');
  form.onsubmit = async event => {
    event.preventDefault(); if (send.disabled) return;
    const question = input.value.trim(); if (!question) return;
    send.disabled = true; input.disabled = true;
    try {
      const answer = await api('/cards/' + card.card_id + '/ask', { question });
      if (state.card?.card_id !== card.card_id) return;
      el('p', question, messages, 'message message-user');
      const response = el('div', undefined, messages, 'message message-assistant'); el('small', answer.demo ? 'AI · Демонстрационный ответ' : 'AI · Ответ по источникам карточки', response);
      el('p', answer.answer || 'В доступных источниках недостаточно данных для ответа.', response);
      history.hidden = false; history.open = true; historyTitle.textContent = 'Диалог по карточке · ' + messages.children.length + ' сообщения';
      input.value = ''; messages.scrollTop = messages.scrollHeight;
    } catch (e) { notice(e.message, true); }
    finally { send.disabled = false; input.disabled = false; }
  };
}
function feedbackReason(card) {
  $('info-title').textContent = 'Что можно улучшить?'; const content = $('info-content'); content.replaceChildren();
  el('p', 'Ваш комментарий поможет оценить полезность разбора.', content);
  const form = el('form', undefined, content, 'scenario-form'); const label = el('label', 'Причина, если хотите уточнить', form);
  const reason = el('textarea', undefined, label); reason.maxLength = 500; reason.rows = 3;
  const submit = el('button', 'Отправить оценку', form, 'primary'); submit.type = 'submit';
  form.onsubmit = async event => { event.preventDefault(); submit.disabled = true; try { await api('/cards/' + card.card_id + '/feedback', { helpful: false, reason: reason.value.trim() || null }); $('info-dialog').close(); notice('Спасибо. Обратная связь сохранена.'); } catch (e) { notice(e.message, true); } finally { submit.disabled = false; } };
  openDialog('info-dialog');
}
function renderScenario(parent, card) {
  el('h3', 'Посчитайте свой сценарий', parent, 'section-heading');
  el('p', 'Введите собственные значения. Расчёт выполняется по формуле и не является прогнозом.', parent, 'scenario-intro');
  const form = el('form', undefined, parent, 'scenario-form'); const kindLabel = el('label', 'Тип расчёта', form); const kind = el('select', undefined, kindLabel); kind.id = 'scenario-kind'; kindLabel.htmlFor = kind.id; kind.setAttribute('aria-label', 'Тип расчёта');
  for (const [value,text] of [['rate','Изменение ставки'],['fx','Изменение валютного курса'],['inflation','Изменение инфляции']]) el('option', text, kind).value = value;
  const fields = el('div', undefined, form, 'scenario-fields'); let inputs = {};
  const output = el('div', undefined, parent, 'scenario-result'); output.setAttribute('role','status');
  function draw() {
    fields.replaceChildren(); output.replaceChildren(); inputs = {};
    const specs = { rate: [['floating_debt_rub','Долг с плавающей ставкой, ₽',0],['delta_rate_pp','Изменение ставки, процентных пунктов']], fx: [['fx_volume','Объём в валюте',0],['spot_from','Начальный курс, ₽',0.000001],['spot_to','Конечный курс, ₽',0.000001]], inflation: [['cogs_rub','Затраты, ₽',0],['pass_through','Доля переноса в затраты, от 0 до 1',0,1],['delta_inflation_pp','Изменение инфляции, процентных пунктов']] };
    for (const [name,text,min,max] of specs[kind.value]) { const label = el('label',text,fields); const input = el('input',undefined,label); input.type='number'; input.step='any'; input.required=true; if(min!==undefined)input.min=min; if(max!==undefined)input.max=max; inputs[name]=input; }
    if (kind.value==='fx') { const label=el('label','Направление',fields); const select=el('select',undefined,label); select.setAttribute('aria-label','Направление'); el('option','Валютные поступления',select).value='receipts'; el('option','Валютные платежи',select).value='payments'; inputs.direction=select; }
  }
  kind.onchange=draw; draw(); const calculate=el('button','Рассчитать',form,'primary'); calculate.type='submit';
  form.onsubmit=async event=>{
    event.preventDefault(); if(calculate.disabled)return; calculate.disabled=true;
    const submittedKind=kind.value;
    try {
      const scenario={scenario_type:submittedKind}; for(const[key,node]of Object.entries(inputs))scenario[key]=key==='direction'?node.value:Number(node.value);
      const result=await api('/cards/'+card.card_id+'/ask',{question:'Посчитать сценарий по введённым данным',scenario});
      if(kind.value!==submittedKind)return;
      const values=result.result||{}; const value=values.delta_annual_rub??values.delta_rub??values.delta_cost_rub;
      if(!Number.isFinite(value))throw Error('Расчёт не вернул конечное значение. Проверьте вводные.');
      const labels={rate:'Изменение годовых процентных расходов',fx:'Изменение рублёвого результата',inflation:'Изменение затрат'};
      const limits={rate:'Весь долг переоценивается на полный год.',fx:'Без учёта хеджирования, налогов и сроков платежей.',inflation:'Линейная оценка по введённой доле переноса.'};
      output.replaceChildren(); el('h3',labels[submittedKind],output); el('strong',new Intl.NumberFormat('ru-RU',{style:'currency',currency:'RUB',maximumFractionDigits:2}).format(value),output); el('p',limits[submittedKind],output); el('p',result.note,output);
      output.scrollIntoView({block:'nearest'});
    }catch(e){notice(e.message,true);}finally{calculate.disabled=false;}
  };
}
async function loadTrace(parent, card) {
  if (!card) return;
  parent.dataset.loaded='true'; parent.replaceChildren(); el('p','Загружаем основания разбора…',parent,'muted');
  try {
    const [trace,basis]=await Promise.all([api('/cards/'+card.card_id+'/trace'),api('/cards/'+card.card_id+'/basis')]);
    if(state.card?.card_id!==card.card_id)return;
    parent.replaceChildren();el('h3','Как получен вывод',parent,'section-heading');el('p','Данные компании, расчёты и существенные этапы проверки.',parent,'muted');
    const features=el('section',undefined,parent,'source-card');el('div','КОНТЕКСТ КОМПАНИИ',features,'layer-label');
    for(const feature of basis.features||[]){const text=FEATURE_NAMES[feature.id]||(feature.id.startsWith('EPS_')?'Чувствительность: '+(FACTORS[feature.id.slice(4)]||feature.id.slice(4)):feature.feature_id);el('p',text+': '+valueText(feature.value),features);}
    const names={'PRM-01':'Извлечение фактов из новости','PRM-05':'Общий разбор события','PRM-06':'Учёт компании и роли','PRM-07':'Независимая проверка утверждений','PRM-08':'Ответ на уточняющий вопрос'};
    let index=0;
    for(const step of trace){const prompt=step.tool||step.step;const known=Object.keys(names).find(key=>String(prompt).includes(key));if(!known)continue;const row=el('section',undefined,parent,'trace-step');el('span',String(++index).padStart(2,'0'),row,'step-number');const copy=el('div',undefined,row);el('h3',names[known],copy);const status=step.status==='ok'?'Завершено':step.status==='cached_event'?'Из кэша':step.status||'Записано';el('p',[step.model,status].filter(Boolean).join(' · '),copy);const done=icon('check',row);done.classList.add('step-icon');}
    if(!index)el('p','Этапы доступны в техническом журнале ниже.',parent,'small-note');
    const raw=el('details',undefined,parent,'trace-raw');el('summary','Технический журнал и расчёты',raw);el('pre',JSON.stringify({trace_id:card.trace_id,calculations:basis.calculations,steps:trace},null,2),raw);
  }catch(e){delete parent.dataset.loaded;parent.replaceChildren();el('p',e.message,parent,'warning');}
}

$('news-form').onsubmit=async event=>{
  event.preventDefault();const text=$('news-text').value.trim(),url=$('news-url').value.trim();
  if(Boolean(text)===Boolean(url)){notice('Укажите что-то одно: текст новости или ссылку.',true);return;}
  const submit=event.submitter; if(submit.disabled)return;submit.disabled=true;
  const client=$('company').value;
  try {
    const body={client_id:client,...(text?{text}:{url})};if($('news-date').value)body.published_at=new Date($('news-date').value).toISOString();
    const result=await api('/events/ingest',body);
    if(client!==$('company').value)return;
    $('news-dialog').close();
    const news={doc_id:result.doc_id,title:result.title||'Ваша новость',snippet:text,published_at:body.published_at,url};
    await generate(news);
  }catch(e){notice(e.message,true);}finally{submit.disabled=false;}
};
function about() {
  $('info-title').textContent='Аналитика с понятным основанием'; const root=$('info-content');root.replaceChildren();
  el('p','Сервис отбирает события по профилю компании и вашей роли. В разборе разделены факт из источника, позиция Альфа-Банка и вывод AI.',root);
  el('p','AI проверяет ссылки и утверждения, однако это не заменяет экспертную оценку. При недостатке данных разбор сохраняет ограничения.',root);
  if(state.runtime.demo)el('p','Сейчас открыт демонстрационный контур с синтетическими компаниями. Оценка качества на настоящих моделях ещё не завершена.',root);
  if($('workspace').hidden)el('p','Для входа используйте код команды или доступный демонстрационный профиль.',root);
  else el('p',state.runtime.model_endpoint_configured?'API моделей настроен. Доступность и проверка ответа определяются при запросе.':'API моделей не настроен. Лента и первоисточники доступны; для AI-разбора администратору нужно подключить LLM_BASE_URL и LLM_API_KEY.',root);
  if (!$('workspace').hidden) button('Контекст компании', root, 'secondary', () => { $('info-dialog').close(); $('context-open').click(); });
  if (state.session.role === 'admin' && !$('workspace').hidden) button('Проверка аналитики банка', root, 'secondary', () => { $('info-dialog').close(); $('admin-open').click(); });
  openDialog('info-dialog');
}
$('about').onclick=about;$('runtime-open').onclick=about;
$('context-open').onclick=()=>{
  const root=$('profile-content');root.replaceChildren();const p=state.profile||{};
  el('h3',industry(p.mainokved_name),root,'section-heading');el('p',[p.region,p.city].filter(Boolean).join(' · ')||'География не указана',root);
  el('p','Роль: '+($('persona').selectedOptions[0]?.textContent||'не выбрана')+'. Она меняет приоритеты и фокус объяснения.',root);
  const chips=el('div',undefined,root,'profile-chips');for(const[key,value]of Object.entries(p.flags||{})){const name=Object.keys(PRODUCT_NAMES).find(k=>key.toLowerCase()===k||key.toLowerCase()==='cl_product_'+k+'_mark');if(name&&['1',1,true].includes(value))el('span',PRODUCT_NAMES[name],chips);}
  el('p','Незаполненные признаки считаются неизвестными. Условия договоров и точный денежный эффект нужно уточнять отдельно.',root);openDialog('profile-dialog');
};
$('admin-open').onclick=()=>{openDialog('admin-dialog');$('load-review').click();};
$('load-review').onclick=async()=>{
  $('load-review').disabled=true;
  try {
    const rows=await api('/internal/review/chunks');$('review').replaceChildren();
    if(!rows.length)el('p','Очередь пуста. Это не подтверждает полноту корпуса.', $('review'),'muted');
    for(const row of rows){const box=el('section',undefined,$('review'));el('h3',row.doc_id+' · страница '+row.page,box);safeLink(box,'Открыть оригинал','/analytics/'+encodeURIComponent(row.doc_id)+'/pages/'+row.page);const label=el('label','Сверенный текст',box);const text=el('textarea',row.text,label);text.rows=7;const numberLabel=el('label','Числа и единицы (JSON)',box);const numbers=el('textarea',row.numbers_json||'[]',numberLabel);const confirm=button('Я сверил оригинал: подтвердить',box,'primary',async()=>{confirm.disabled=true;try{await api('/internal/review/chunks/'+encodeURIComponent(row.chunk_id),{confirmed:true,text:text.value,numbers:JSON.parse(numbers.value)});confirm.textContent='Подтверждено';}catch(e){confirm.disabled=false;notice(e.message,true);}});}
  }catch(e){notice(e.message,true);}finally{$('load-review').disabled=false;}
};
(async()=>{
  try {
    const config=await api('/public-config');state.runtime.demo=config.demo;
    $('demo-picker').hidden=!(config.demo_accounts||[]).length;
    for(const account of config.demo_accounts||[]){const b=button(industry(account.label),$('accounts'),'',()=>login(account.token).catch(e=>notice(e.message,true)));b.title=account.label;}
    const session=await fetch('/session');if(session.ok)await start();
  }catch(e){notice(e.message,true);}
})();
