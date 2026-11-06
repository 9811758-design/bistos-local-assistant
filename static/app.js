"use strict";

const state = {
  user: null, csrf: "", products: [], allProducts: [], documents: [], model: null, view: "ask",
  selectedProduct: "", currentJob: null, jobStarted: 0, pollTimer: null,
  docFilter: "", docState: "", docQuery: "", docPage: 1, docPageSize: 10,
  productQuery: "", productPage: 1, expandedProductFamily: "", question: "", useAI: true,
  inquiryMode: "product",
  locale: window.BistosI18n.locale, setup: false, bootError: null, busyCount: 0,
  authDraft: { username: "", password: "", displayName: "" },
  sessionVersion: 0, translationTask: null, translationTimer: null,
  sourceFilters: {}, libraryFilters: {}, manualDefaults: [], manualSelections: {},
  librarySelection: new Set(), libraryAvailability: "", libraryBatchResult: null, bulkBusy: false, libraryTrash: "",
  followupParentJob: null, serviceCases: [], serviceCaseQuery: "", serviceCaseMarket: "", serviceCaseBusy: false, serviceCaseAnswer: "", serviceModels: [], serviceSources: [], serviceSelectedModel: "", serviceModelQuery: "", serviceRegistryLoaded: false, serviceCaseRequest: 0, serviceCaseSort: "newest", serviceCaseDateField: "received", serviceCasePage: 1, serviceCaseTotal: 0, serviceCaseHasMore: false, serviceCasePageSize: 5,
  serviceGuide: null, serviceGuides: [], serviceGuideBusy: false, serviceGuideRequest: 0, serviceGuideListRequest: 0, serviceGuidesLoaded: false, serviceGuideModeFilter: "all", serviceGuideListPage: 1,
};
const app = document.querySelector("#app");
const dialog = document.querySelector("#dialog");
const kindKeys = { user: "kindUser", quick: "kindQuick", service: "kindService", other: "kindOther" };
const stateKeys = { draft: "stateDraft", review: "stateReview", published: "statePublished", withdrawn: "stateWithdrawn", processing_failed: "stateFailed" };
const capabilityKeys = { ask: "capAsk", read_service: "capService", manage_documents: "capDocuments", manage_users: "capUsers" };
const paths = {
  chat: ["M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z"],
  book: ["M4 3h6a3 3 0 0 1 3 3v15a4 4 0 0 0-4-2H4Z", "M13 6a3 3 0 0 1 3-3h5v16h-4a4 4 0 0 0-4 2"],
  clock: ["M12 8v4l3 2", "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"],
  users: ["M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2", "M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z", "M22 21v-2a4 4 0 0 0-3-3.9", "M16 3.1a4 4 0 0 1 0 7.8"],
  settings: ["M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z", "m9 3-.6 2-2 .9-1.9-.5L2 9l1.4 1.5v3L2 15l2.5 3.6 1.9-.5 2 .9.6 2h6l.6-2 2-.9 1.9.5L22 15l-1.4-1.5v-3L22 9l-2.5-3.6-1.9.5-2-.9L15 3Z"],
  file: ["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z", "M14 2v6h6", "M8 13h8", "M8 17h6"],
  arrow: ["M12 19V5", "m5 12 7-7 7 7"],
  plus: ["M12 5v14", "M5 12h14"],
  close: ["M18 6 6 18", "m6 6 12 12"],
  exit: ["M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4", "m16 17 5-5-5-5", "M21 12H9"],
  chevron: ["m9 18 6-6-6-6"],
  download: ["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4", "m7 10 5 5 5-5", "M12 15V3"],
  upload: ["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4", "m7 8 5-5 5 5", "M12 3v12"],
  info: ["M12 16v-4", "M12 8h.01", "M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z"],
  wrench: ["M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.1 2.1-2.8-.7-.7-2.8Z"],
  search: ["M21 21l-4.3-4.3", "M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z"],
  shield: ["M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Z", "m9 12 2 2 4-4"],
};

function el(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "checked" || key === "disabled" || key === "hidden" || key === "required" || key === "selected") node[key] = Boolean(value);
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("class", "icon"); svg.setAttribute("aria-hidden", "true");
  for (const data of paths[name] || paths.file) {
    const path = document.createElementNS(svg.namespaceURI, "path");
    path.setAttribute("d", data); svg.append(path);
  }
  return svg;
}
function button(label, action, variant = "", iconName = null) {
  return el("button", { type: "button", class: `btn ${variant}`, onclick: action }, iconName && icon(iconName), label);
}
function link(label, href, className = "") {
  return el("a", { href, class: className, target: "_blank", rel: "noopener noreferrer" }, label);
}
function badge(text, variant = "") { return el("span", { class: `badge ${variant}` }, text); }
function notice(text, variant = "", role = "status") {
  return el("div", { class: `notice ${variant}`, role }, icon("info"), el("div", {}, el("p", {}, text)));
}
function field(label, input, help = "") {
  return el("div", { class: "field" }, el("label", { for: input.id }, label), input, help && el("p", { class: "form-help" }, help));
}
function input(id, type = "text", attributes = {}) { return el("input", { id, name: id, type, ...attributes }); }
function checkbox(id, label, checked = false, help = "") {
  return el("label", { class: "check-label", for: id },
    input(id, "checkbox", { checked }), el("span", {}, label, help && el("small", {}, help)));
}
function has(capability) { return Boolean(state.user?.capabilities?.includes(capability)); }
function docUrl(documentId, page = 1) { return `/api/documents/${encodeURIComponent(documentId)}/pdf#page=${Number(page) || 1}`; }
function t(key, params) { return window.BistosI18n.t(key, params); }
function ui(source, params) { return window.BistosI18n.translateText(source, params); }
function kindLabel(value) { return kindKeys[value] ? t(kindKeys[value]) : value; }
function stateLabel(value) { return stateKeys[value] ? t(stateKeys[value]) : value; }
function capabilityLabel(value) { return capabilityKeys[value] ? t(capabilityKeys[value]) : value; }
function number(value) { return window.BistosI18n.formatCount(value); }
function syncLanguage() {
  document.documentElement.lang = state.locale;
  const skip = document.querySelector(".skip-link");
  if (skip) skip.textContent = ui("본문으로 이동");
  const loading = app.querySelector(".boot-screen [role='status']");
  if (loading) loading.textContent = ui("프로그램에 연결하고 있습니다.");
}
function languageControl(id = "language") {
  const select = el("select", { id, class: "language-select", "aria-label": t("language"), disabled: state.busyCount > 0 },
    el("option", { value: "ko", selected: state.locale === "ko" }, t("korean")),
    el("option", { value: "en", selected: state.locale === "en" }, t("english")));
  select.addEventListener("change", () => {
    if (state.busyCount > 0) { select.value = state.locale; return; }
    const username = document.querySelector("#username"); const password = document.querySelector("#password"); const displayName = document.querySelector("#display_name");
    if (username) state.authDraft.username = username.value; if (password) state.authDraft.password = password.value; if (displayName) state.authDraft.displayName = displayName.value;
    const drafts = [...app.querySelectorAll("input[id], select[id], textarea[id]")]
      .filter(control => !control.classList.contains("language-select") && control.type !== "file")
      .map(control => ({ id: control.id, value: control.value, checked: control.checked }));
    state.locale = window.BistosI18n.setLocale(select.value); syncLanguage();
    if (state.view === "service") { state.serviceCaseAnswer = ""; state.serviceCases = []; state.serviceCaseBusy = false; state.serviceCasePage = 1; state.serviceCaseTotal = 0; state.serviceCaseHasMore = false; state.serviceCaseRequest++; state.serviceGuide = null; state.serviceGuides = []; state.serviceGuideBusy = false; state.serviceGuidesLoaded = false; state.serviceGuideRequest++; state.serviceGuideListRequest++; state.serviceGuideModeFilter = "all"; state.serviceGuideListPage = 1; }
    document.querySelector("#toast").hidden = true;
    renderCurrent();
    for (const draft of drafts) {
      const control = document.getElementById(draft.id);
      if (control) { control.value = draft.value; if ("checked" in control) control.checked = draft.checked; }
    }
    document.getElementById(id)?.focus();
  });
  return el("label", { class: "language-control", for: id }, el("span", {}, t("language")), select);
}
function brand() { return el("div", { class: "brand" }, el("img", { class: "brand-logo", src: "/static/bistos-logo.png", alt: "Bistos" }), el("small", { class: "brand-subtitle" }, t("productAssistant"))); }
function renderCurrent() {
  if (state.bootError) renderBootError(state.bootError);
  else if (state.user) renderShell();
  else renderAuth(state.setup);
}
function toast(message) {
  const target = document.querySelector("#toast");
  target.textContent = message; target.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { target.hidden = true; }, 6000);
}
function errorText(error) { return error instanceof Error ? ui(error.uiSource || error.message, error.uiParams) : ui("요청을 처리하지 못했습니다."); }
function requestError(source, status) {
  const params = { status };
  const error = new Error(ui(source, params));
  error.uiSource = source; error.uiParams = params; error.status = status;
  return error;
}
async function api(path, options = {}) {
  const method = options.method || "GET";
  const headers = new Headers(options.headers || {});
  if (method !== "GET" && state.csrf) headers.set("X-CSRF-Token", state.csrf);
  let body = options.body;
  if (body && !(body instanceof FormData)) { headers.set("Content-Type", "application/json"); body = JSON.stringify(body); }
  let response;
  try { response = await fetch(path, { ...options, method, headers, body, credentials: "same-origin" }); }
  catch { throw requestError("프로그램 서버에 연결할 수 없습니다. 실행 창이 켜져 있는지 확인해 주세요."); }
  if (!response.ok) {
    let message = "요청을 처리하지 못했습니다. ({status})";
    try {
      const data = await response.json();
      message = typeof data.detail === "string" ? data.detail : "입력한 내용과 필수 항목을 확인해 주세요.";
    } catch { message = "서버 응답을 읽지 못했습니다. ({status})"; }
    throw requestError(message, response.status);
  }
  return options.blob ? response.blob() : response.status === 204 ? null : response.json();
}
async function busy(control, task, label = ui("처리 중…")) {
  if (control.disabled) return;
  const previous = [...control.childNodes];
  state.busyCount++;
  for (const selector of document.querySelectorAll(".language-select")) selector.disabled = true;
  control.disabled = true; control.replaceChildren(label);
  try { return await task(); }
  finally {
    state.busyCount--;
    for (const selector of document.querySelectorAll(".language-select")) selector.disabled = state.busyCount > 0;
    if (control.isConnected) { control.disabled = false; control.replaceChildren(...previous); }
  }
}
function showDialog(title, body) {
  if (dialog.open) dialog.close();
  dialog.replaceChildren(
    el("div", { class: "dialog-header" }, el("h2", { id: "dialog-title" }, title),
      el("button", { type: "button", class: "btn ghost icon-button", "aria-label": ui("닫기"), onclick: () => dialog.close() }, icon("close"))),
    el("div", { class: "dialog-body", translate: "no" }, body));
  dialog.showModal();
}
function dialogError(form, error) {
  form.querySelector(".form-error")?.remove();
  const message = notice(errorText(error), "error", "alert"); message.classList.add("form-error");
  form.prepend(message); message.scrollIntoView({ block: "nearest" });
}
function pageHeading(title, description, actions = []) {
  return el("div", { class: "page-heading" },
    el("div", {}, el("h1", {}, title), el("p", {}, description)),
    actions.length > 0 && el("div", { class: "actions" }, actions));
}
function empty(title, description, action = null, iconName = "book") {
  return el("div", { class: "empty" }, icon(iconName), el("h2", {}, title), el("p", {}, description), action && el("div", { class: "actions" }, action));
}
function productSelect(id, includeAll = false, selected = state.selectedProduct) {
  const productSource = id === "upload-product" || id === "new-product-product" || id === "held-product" || (id === "document-filter" && has("manage_documents")) ? (state.allProducts.length ? state.allProducts : state.products) : state.products;
  const option = product => el("option", { value: product.id, selected: product.id === selected },
    product.name === product.id ? product.id : `${product.id} · ${product.name}`);
  return el("select", { id, name: id, required: !includeAll },
    el("option", { value: "" }, includeAll ? t("allProducts") : t("chooseProduct")),
    groupProducts(productSource).map(group => group.products.length === 1 ? option(group.products[0]) :
      el("optgroup", { label: ui("{family} 제품군", { family: group.id }) },
        id === "document-filter" && el("option", { value: `family:${group.id}`, selected: selected === `family:${group.id}` },
          ui("{family} 제품군 전체", { family: group.id })),
        group.products.map(option))));
}
function productFamilyId(id) {
  const bt = /^BT-(\d+)(?=$|[-A-Za-z])/i.exec(id);
  if (bt) return `BT-${bt[1]}`;
  // Only group other names when their exact base product is registered.
  const base = state.products.filter(product => product.id !== id && id.startsWith(`${product.id}-`))
    .sort((a, b) => b.id.length - a.id.length)[0];
  return base?.id || id;
}
function groupProducts(products) {
  const groups = new Map();
  for (const product of products) {
    const family = productFamilyId(product.id);
    if (!groups.has(family)) groups.set(family, { id: family, products: [] });
    groups.get(family).products.push(product);
  }
  const compare = (a, b) => a.id.localeCompare(b.id, undefined, { numeric: true });
  return [...groups.values()].sort(compare).map(group => ({ ...group, products: group.products.sort(compare) }));
}
function libraryProductLabel(value) {
  return value.startsWith("family:") ? ui("{family} 제품군", { family: value.slice(7) }) : value;
}
function currentProduct() { return state.products.find(product => product.id === state.selectedProduct); }
function manualDefault(productId = state.selectedProduct, country = "") {
  const active = state.manualDefaults.filter(item => item.active !== false && item.product_id === productId);
  const match = active.find(item => item.product_id === productId &&
    (!country || item.country === country));
  if (country) return match;
  return active.length === 1 ? match : undefined;
}
function applyManualDefault(productId) {
  const selectedId = state.manualSelections[productId];
  const selectedDocument = selectedId && state.documents.find(item => item.id === selectedId);
  if (selectedDocument && selectedDocument.product_id === productId && eligibleSource(selectedDocument)) {
    state.sourceFilters = { country: selectedDocument.country || "", document_id: selectedDocument.id };
    return;
  }
  const baseline = manualDefault(productId);
  if (!baseline || !baseline.document_id) return;
  const document = state.documents.find(item => item.id === baseline.document_id);
  if (!document || !eligibleSource(document)) return;
  state.manualSelections[productId] = document.id;
  state.sourceFilters = { country: baseline.country || document.country || "", document_id: document.id };
}
function resetQuestionScope() {
  state.sourceFilters = {};
  state.followupParentJob = null;
}
function sourceLanguage(value = "") {
  const language = value.trim().toLowerCase();
  return ["ko", "kor", "korean", "한국어", "국문"].includes(language) ? "ko" :
    ["en", "eng", "english", "영문"].includes(language) ? "en" : language;
}
function sourceLanguageLabel(value) {
  return sourceLanguage(value) === "ko" ? t("korean") : sourceLanguage(value) === "en" ? t("english") : value;
}
function matchesSourceFilters(document, filters = {}) {
  return Object.entries({ document_id: "id", document_language: "language", country: "country", kind: "kind", revision: "revision" })
    .every(([filter, key]) => !filters[filter] || (key === "language" ?
      sourceLanguage(document[key]) === sourceLanguage(filters[filter]) : document[key] === filters[filter]));
}
function pendingApproval(value) { return ["pending", "결재 진행x", "결재 진행 x", "결재진행x"].includes((value || "").toLowerCase()); }
function blockedApproval(value) { return pendingApproval(value) || ["rejected", "unapproved", "approval_only", "quarantined", "invalid", "excluded", "미승인"].includes((value || "").toLowerCase()); }
function blockedValidity(value) { return ["invalid", "expired", "history", "superseded", "withdrawn", "quarantined", "excluded", "metadata_conflict", "효력상실"].includes((value || "").toLowerCase()); }
function eligibleSource(document) {
  return document.enabled !== false && document.state === "published" &&
    !blockedApproval(document.approval_status) && !blockedValidity(document.validity);
}
function sourceDocuments() {
  return state.documents.filter(document => document.product_id === state.selectedProduct &&
    eligibleSource(document) && (document.kind !== "service" || has("read_service")));
}
function metadataSelect(id, documents, key, value, allLabel, labelFor = value => value) {
  const values = [...new Set(documents.map(document => key === "language" ? sourceLanguage(document[key]) : document[key]).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  return el("select", { id }, el("option", { value: "" }, allLabel),
    values.map(item => el("option", { value: item, selected: item === value }, labelFor(item))));
}
function renderSourceFilters() {
  const container = document.querySelector("#source-filters");
  if (!container) return;
  const docs = sourceDocuments();
  if (state.sourceFilters.document_id && !docs.some(document => document.id === state.sourceFilters.document_id)) state.sourceFilters.document_id = "";
  const controls = [
    ["document_language", "language", ui("근거 문서 언어"), ui("모든 문서 언어"), sourceLanguageLabel],
    ["country", "country", ui("대상 국가 / 시장"), ui("모든 국가 / 시장")],
    ["kind", "kind", ui("문서 종류"), ui("모든 문서 종류"), kindLabel],
    ["revision", "revision", ui("개정"), ui("모든 개정")],
  ].map(([filter, key, label, allLabel, labelFor]) => {
    const control = metadataSelect(`source-${filter}`, docs, key, state.sourceFilters[filter], allLabel, labelFor);
    control.addEventListener("change", () => {
      state.sourceFilters[filter] = control.value; state.sourceFilters.document_id = "";
      if (state.selectedProduct) delete state.manualSelections[state.selectedProduct];
      renderSourceFilters(); renderReferencePane(document.querySelector("#reference-pane"), state.currentJob?.result);
    });
    return field(label, control);
  });
  const baseline = manualDefault();
  const matches = docs.filter(document => matchesSourceFilters(document, { ...state.sourceFilters, document_id: "" }));
  const selected = el("select", { id: "source-document" }, el("option", { value: "" }, ui("자동으로 찾기")),
    matches.map(document => el("option", { value: document.id, selected: document.id === state.sourceFilters.document_id },
      `${document.title} · ${sourceLanguageLabel(document.language)} · ${document.country || ui("국가 미확인")} · ${document.revision || ui("개정 미지정")}`)));
  selected.addEventListener("change", () => {
    state.sourceFilters.document_id = selected.value;
    if (state.selectedProduct && selected.value) state.manualSelections[state.selectedProduct] = selected.value;
    renderReferencePane(document.querySelector("#reference-pane"), state.currentJob?.result);
  });
  container.replaceChildren(el("summary", {}, ui("검색 범위 직접 설정 (선택)")),
    ...(baseline ? [el("p", { class: "default-manual-note" }, ui("관리자 기준 매뉴얼: {title} · {country}", {
      title: state.documents.find(item => item.id === baseline.document_id)?.title || baseline.document_id,
      country: baseline.country || ui("국가 미확인"),
    }))] : []), el("div", { class: "source-filter-grid" }, controls,
    el("div", { class: "full" }, field(ui("사용할 매뉴얼"), selected))),
    el("p", { class: "form-help" }, ui("답변 언어와 별도로 원문 언어·국가·개정을 선택할 수 있습니다. 최신·유효 여부가 미확인인 문서는 담당자에게 확인하세요.")));
}
function documentStatusDetails(document) {
  const approval = (document.approval_status || "").toLowerCase() === "approved" ? ui("승인 확인") :
    pendingApproval(document.approval_status) ? ui("결재 미진행 · 답변 제외") :
    blockedApproval(document.approval_status) ? ui("승인 대상 제외") : ui("승인 여부 미확인");
  const validity = (document.validity || "").toLowerCase() === "valid" ? ui("유효 확인") :
    (document.validity || "").toLowerCase() === "metadata_conflict" ? ui("메타데이터 불일치 · 검토 필요") :
    blockedValidity(document.validity) ? ui("유효 자료에서 제외") : ui("최신·유효 여부 미확인");
  return el("div", { class: "manual-status-details small muted" },
    el("span", {}, approval), el("span", {}, validity),
    el("span", {}, document.approval_date ? ui("승인일: {date}", { date: document.approval_date }) : ui("승인일 미확인")),
    document.needs_review && el("span", {}, ui("표·그림 원문 검토 필요")));
}
function libraryDocuments() {
  const query = state.docQuery.trim().toLocaleLowerCase();
  return state.documents.filter(document => (!state.docFilter || (state.docFilter.startsWith("family:") ?
    productFamilyId(document.product_id) === state.docFilter.slice(7) : document.product_id === state.docFilter)) &&
    (!state.docState || document.state === state.docState) &&
    documentMatchesTrashFilter(document, state.libraryTrash) &&
    (!state.libraryAvailability || (state.libraryAvailability === "enabled" ? document.enabled !== false : document.enabled === false)) &&
    matchesSourceFilters(document, state.libraryFilters) &&
    (!query || [document.title, document.product_id, document.revision, document.original_name, document.country,
      document.language, document.approval_date, document.source_record, ...(document.source_files || []).map(file => file.name)]
      .filter(Boolean).join(" ").toLocaleLowerCase().includes(query)));
}
function documentMatchesTrashFilter(document, filter = state.libraryTrash) {
  return filter === "all" || (filter === "trashed" ? document.trashed === true : document.trashed !== true);
}
function matchingProducts(query = state.productQuery) {
  const normalize = value => String(value || "").toLocaleLowerCase().replace(/[\s_-]+/g, "");
  const normalized = normalize(query);
  return state.products.filter(product => !normalized ||
    [product.id, product.name, ...(product.aliases || [])].some(value => normalize(value).includes(normalized)));
}
function matchingProductGroups() {
  const families = new Set(matchingProducts().map(product => productFamilyId(product.id)));
  return groupProducts(state.products).filter(group => families.has(group.id));
}
function productPage(products = matchingProducts()) {
  const size = 5; const pages = Math.max(1, Math.ceil(products.length / size));
  state.productPage = Math.min(Math.max(1, state.productPage), pages);
  const start = (state.productPage - 1) * size;
  return { items: products.slice(start, start + size), page: state.productPage, pages, total: products.length, start: products.length ? start + 1 : 0, end: Math.min(start + size, products.length) };
}
function libraryPage(docs = libraryDocuments()) {
  const size = state.docPageSize === 20 ? 20 : 10; const pages = Math.max(1, Math.ceil(docs.length / size));
  state.docPage = Math.min(Math.max(1, state.docPage), pages);
  const start = (state.docPage - 1) * size;
  return { items: docs.slice(start, start + size), page: state.docPage, pages, total: docs.length, start: docs.length ? start + 1 : 0, end: Math.min(start + size, docs.length) };
}
function clearLibrarySelection() { state.librarySelection.clear(); state.libraryBatchResult = null; }
function toggleLibraryPage(pageItems, checked) {
  if (!has("manage_documents") || state.bulkBusy) return;
  const proposed = new Set([...state.librarySelection, ...pageItems.map(item => item.id)]);
  if (checked && proposed.size > 500) { toast(ui("한 번에 최대 500개까지 선택할 수 있습니다.")); return; }
  for (const document of pageItems) checked ? state.librarySelection.add(document.id) : state.librarySelection.delete(document.id);
  renderDocumentList();
}
async function bulkAvailability(enabled) {
  if (!has("manage_documents") || state.bulkBusy) return;
  const ids = [...state.librarySelection];
  if (!ids.length) { toast(ui("문서를 선택하세요.")); return; }
  if (state.librarySelection.size > 500) { toast(ui("한 번에 최대 500개까지 선택할 수 있습니다.")); return; }
  state.bulkBusy = true; renderDocumentList();
  const session = state.sessionVersion;
  try {
    const result = await api("/api/documents/bulk-availability", { method: "POST", body: { document_ids: ids, enabled } });
    if (session !== state.sessionVersion) return;
    state.libraryBatchResult = result;
    state.librarySelection.clear();
    await refreshData();
    if (session === state.sessionVersion && state.view === "documents") renderDocuments(document.querySelector("#main"));
  } catch (error) { toast(errorText(error)); }
  finally { state.bulkBusy = false; renderDocumentList(); }
}
async function bulkLifecycle(action) {
  if (!has("manage_documents") || state.bulkBusy || !state.librarySelection.size) return;
  const ids = [...state.librarySelection]; state.bulkBusy = true; const session = state.sessionVersion; renderDocumentList();
  try {
    const endpoint = "/api/documents/bulk-trash";
    const result = await api(endpoint, { method: "POST", body: { document_ids: ids, trashed: action === "trash", reason: action === "trash" ? ui("관리자 자료 정리") : ui("관리자 복원") } });
    if (session !== state.sessionVersion) return;
    state.libraryBatchResult = result; state.librarySelection.clear(); await refreshData();
    if (state.view === "documents") renderDocuments(document.querySelector("#main"));
  } catch (error) { toast(errorText(error)); }
  finally { state.bulkBusy = false; renderDocumentList(); }
}

async function boot() {
  syncLanguage();
  document.title = `${t("productAssistant")} · Bistos`;
  const loadingTitle = app.querySelector(".boot-screen h1");
  if (loadingTitle) loadingTitle.textContent = t("productAssistant");
  try {
    const status = await api("/api/status");
    if (status.setup_required) return renderAuth(true);
    try {
      const session = await api("/api/me");
      state.user = session.user; state.csrf = session.csrf;
    } catch (error) { if (error.status === 401) return renderAuth(false); throw error; }
    await refreshData(); renderShell();
  } catch (error) {
    renderBootError(error);
  }
}
function renderBootError(error) {
  state.bootError = error;
  document.title = `${t("offline")} · Bistos`;
  app.replaceChildren(el("main", { id: "main", class: "boot-screen" }, brand(),
    el("h1", {}, t("offline")), el("p", { role: "alert" }, errorText(error)),
    button(t("reconnect"), boot, "", "search"), languageControl("language-boot")));
}
async function refreshData() {
  const productRequests = [api("/api/products"), api("/api/documents"), api("/api/model")];
  if (has("manage_documents")) productRequests.push(api("/api/products?include_unavailable=true"));
  const [products, documents, model, catalog] = await Promise.all(productRequests);
  state.documents = documents; state.products = products; state.allProducts = catalog || products; state.model = model;
  if (state.librarySelection?.size) {
    const valid = new Set(documents.map(document => document.id));
    state.librarySelection = new Set([...state.librarySelection].filter(id => valid.has(id)));
  }
  try { state.manualDefaults = await api("/api/manual-defaults"); }
  catch (error) { if (error.status !== 404 && error.status !== 403) throw error; state.manualDefaults = []; }
  if (state.selectedProduct && !state.products.some(product => product.id === state.selectedProduct)) state.selectedProduct = "";
  if (!state.selectedProduct && state.products.length === 1) state.selectedProduct = state.products[0].id;
  if (state.useAI === null) state.useAI = Boolean(model.ready && model.enabled);
  if (!model.ready || !model.enabled) state.useAI = false;
}
function renderAuth(setup) {
  clearTimeout(state.pollTimer); state.csrf = ""; state.user = null; state.useAI = null;
  state.currentJob = null; state.sessionVersion++; clearTranslationTask();
  state.sourceFilters = {}; state.libraryFilters = {}; state.manualDefaults = []; state.manualSelections = {}; state.followupParentJob = null; state.docPage = 1; state.docPageSize = 10;
  state.docFilter = ""; state.docState = ""; state.docQuery = "";
  clearLibrarySelection(); state.libraryAvailability = ""; state.libraryTrash = ""; state.bulkBusy = false;
  state.selectedProduct = ""; state.products = []; state.documents = [];
  state.serviceCases = []; state.serviceCaseQuery = ""; state.serviceCaseMarket = ""; state.serviceCaseBusy = false; state.serviceCaseAnswer = ""; state.serviceModels = []; state.serviceSources = []; state.serviceSelectedModel = ""; state.serviceModelQuery = ""; state.serviceRegistryLoaded = false; state.serviceCaseRequest++; state.serviceCaseSort = "newest"; state.serviceCaseDateField = "received"; state.serviceCasePage = 1; state.serviceCaseTotal = 0; state.serviceCaseHasMore = false; state.serviceGuide = null; state.serviceGuides = []; state.serviceGuideBusy = false; state.serviceGuideRequest++; state.serviceGuideListRequest++; state.serviceGuidesLoaded = false; state.serviceGuideModeFilter = "all"; state.serviceGuideListPage = 1;
  state.productQuery = ""; state.productPage = 1; state.expandedProductFamily = "";
  state.setup = setup; state.bootError = null; syncLanguage();
  document.documentElement.lang = state.locale; document.title = `${setup ? t("firstStart") : t("signIn")} · Bistos`;
  const username = input("username", "text", { required: true, autocomplete: "username", minlength: setup ? 3 : 1, maxlength: 64, placeholder: t("username"), pattern: "[A-Za-z0-9_.\\-]+" });
  const password = input("password", "password", { required: true, autocomplete: setup ? "new-password" : "current-password", minlength: setup ? 6 : 1, maxlength: 128, placeholder: setup ? t("passwordNew") : t("password") });
  const displayName = input("display_name", "text", { required: true, autocomplete: "name", maxlength: 60, placeholder: t("displayName") });
  username.value = state.authDraft.username; password.value = state.authDraft.password; displayName.value = state.authDraft.displayName;
  const submit = el("button", { type: "submit", class: "btn" }, setup ? t("createAdmin") : t("login"));
  const form = el("form", { class: "form-stack" },
    field(t("username"), username, setup ? ui("영문, 숫자, 밑줄, 마침표, 하이픈을 사용하세요.") : ""),
    setup && field(t("displayName"), displayName),
    field(t("password"), password, setup ? t("firstAdminHelp") : ""),
    submit);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!form.reportValidity() || submit.disabled) return;
    await busy(submit, async () => {
      try {
        const credentials = { username: username.value.trim(), password: password.value };
        if (setup) await api("/api/setup", { method: "POST", body: { ...credentials, display_name: displayName.value.trim() } });
        const session = await api("/api/login", { method: "POST", body: credentials });
        state.user = session.user; state.csrf = session.csrf; state.authDraft = { username: "", password: "", displayName: "" }; password.value = "";
        await refreshData(); renderShell();
      } catch (error) {
        if (state.user) renderBootError(error);
        else dialogError(form, error);
      }
    }, setup ? t("setupBusy") : t("loginBusy"));
  });
  app.replaceChildren(el("main", { id: "main", class: "auth-screen" },
    el("section", { class: "auth-story", "aria-label": ui("프로그램 소개") }, brand(),
      el("div", {}, el("div", { class: "eyebrow" }, "PRODUCT KNOWLEDGE"), el("h1", {}, t("introTitle"), el("br"), t("introTitle2")),
        el("p", {}, t("introText"))),
      el("small", {}, "Bistos product knowledge workspace")),
    el("section", { class: "auth-panel" }, el("div", { class: "auth-form" },
      el("div", { class: "auth-toolbar" }, languageControl("language-login")),
      el("h2", {}, setup ? t("firstStart") : t("signIn")),
      el("p", {}, setup ? t("setupHelp") : t("signInHelp")), form))));
  username.focus();
}
function renderShell() {
  state.bootError = null; syncLanguage();
  const menus = [
    ["ask", t("ask"), "chat", has("ask")],
    ["service", t("serviceCases"), "wrench", has("read_service")],
    ["documents", t("manuals"), "book", true],
    ["history", t("history"), "clock", has("ask")],
    ["users", t("users"), "users", has("manage_users")],
    ["settings", t("settings"), "settings", has("manage_documents")],
  ].filter(item => item[3]);
  if (!menus.some(item => item[0] === state.view)) state.view = menus[0]?.[0] || "documents";
  const selectedMenu = menus.find(item => item[0] === state.view);
  const productCounts = new Map();
  for (const document of state.documents.filter(eligibleSource)) productCounts.set(document.product_id, (productCounts.get(document.product_id) || 0) + 1);
  const productSearch = input("sidebar-product-search", "search", { class: "product-search", placeholder: ui("제품명 검색"), "aria-label": ui("제품 검색"), value: state.productQuery });
  const productList = el("div", { id: "sidebar-product-results", class: "product-results" });
  function renderProductResults() {
    const groups = matchingProductGroups();
    const page = productPage(groups);
    const productButton = product => el("button", {
      class: `product-link ${state.selectedProduct === product.id ? "selected" : ""}`, type: "button",
      "data-product-id": product.id, "aria-pressed": String(state.selectedProduct === product.id),
      onclick: () => selectSidebarProduct(product.id),
    }, el("span", {}, product.id), el("span", { class: "count" }, number(productCounts.get(product.id) || 0)));
    productList.replaceChildren(...page.items.map(group => {
      const total = group.products.length;
      if (total === 1) return productButton(group.products[0]);
      const open = state.expandedProductFamily === group.id || (state.productQuery.trim() && groups.length === 1);
      const details = el("details", { class: "product-family", open: open || null },
        el("summary", {}, el("span", {}, group.id), el("span", { class: "count" }, ui("{count}개 모델", { count: number(total) }))),
        el("div", { class: "product-variants" }, group.products.map(productButton),
          button(ui("제품군 매뉴얼 보기"), () => {
            clearLibrarySelection(); state.docFilter = `family:${group.id}`; state.docPage = 1; state.view = "documents";
            state.expandedProductFamily = group.id; renderShell();
          }, "ghost compact")));
      details.addEventListener("toggle", () => {
        if (details.open) state.expandedProductFamily = group.id;
        else if (state.expandedProductFamily === group.id) state.expandedProductFamily = "";
      });
      return details;
    }));
    if (!page.total) productList.append(el("p", { class: "small muted", role: "status" }, ui("일치하는 제품이 없습니다.")));
    const footer = el("div", { class: "product-pager" },
      el("span", { class: "product-result-count", "aria-live": "polite" }, ui("제품군 {start}-{end} / {total}", { start: number(page.start), end: number(page.end), total: number(page.total) })),
      button(ui("이전"), () => { state.productPage--; renderProductResults(); productSearch.focus(); }, "ghost compact"),
      button(ui("다음"), () => { state.productPage++; renderProductResults(); productSearch.focus(); }, "ghost compact"));
    footer.querySelectorAll("button")[0].disabled = page.page <= 1;
    footer.querySelectorAll("button")[1].disabled = page.page >= page.pages;
    productList.append(footer);
  }
  productSearch.addEventListener("input", () => { state.productQuery = productSearch.value; state.productPage = 1; renderProductResults(); });
  renderProductResults();
  const localStatus = state.model?.ready && state.model?.enabled ? t("localAI") : t("manualSearch");
  app.replaceChildren(el("div", { class: "app-layout" },
    el("aside", { class: "sidebar" }, brand(),
      el("nav", { class: "nav", "aria-label": ui("주 메뉴") }, menus.map(([id, label, iconName]) => el("button", {
        type: "button", class: "nav-item", "aria-current": id === state.view ? "page" : null,
        onclick: () => navigate(id),
      }, icon(iconName), label))),
      el("p", { class: "sidebar-label" }, t("products")),
      el("div", { class: "product-browser" }, productSearch,
        el("p", { id: "selected-product-label", class: "small muted" }, state.selectedProduct ? ui("선택 제품: {product}", { product: state.selectedProduct }) : ui("제품군을 펼쳐 세부 모델을 선택하세요.")), productList),
      el("div", { class: "sidebar-footer" },
        el("div", { class: "status-inline" }, icon("shield"), el("span", {}, t("localData"))),
        el("div", { class: "user-block" }, el("span", { class: "avatar", "aria-hidden": "true" }, state.user.display_name.slice(0, 1)),
          el("div", { class: "user-info" }, el("strong", {}, state.user.display_name), el("span", {}, state.user.username)),
          el("button", { type: "button", class: "btn ghost icon-button", "aria-label": ui("로그아웃"), title: ui("로그아웃"), onclick: logout }, icon("exit"))))),
    el("div", { class: "workspace" },
      el("header", { class: "topbar" }, el("div", { class: "breadcrumb" }, el("span", {}, t("home")), el("span", { class: "slash", "aria-hidden": "true" }, "/"), el("strong", {}, selectedMenu?.[1])),
        el("div", { class: "topbar-actions" }, languageControl("language-shell"), el("div", { class: "status-inline" }, el("span", { class: `dot ${state.model?.ready && state.model?.enabled ? "ready" : ""}`, "aria-hidden": "true" }), el("span", {}, localStatus)))),
      el("main", { id: "main", class: "main-content", tabindex: "-1" }))));
  document.title = `${selectedMenu?.[1] || t("home")} · Bistos`;
  renderView();
}
function selectSidebarProduct(productId) {
  if (!state.products.some(product => product.id === productId)) return;
  state.selectedProduct = productId; resetQuestionScope();
  state.expandedProductFamily = productFamilyId(productId);
  if (state.currentJob && !["queued", "running"].includes(state.currentJob.status)) state.currentJob = null;
  if (state.view === "documents" || !has("ask")) {
    clearLibrarySelection(); state.view = "documents"; state.docFilter = productId; state.docPage = 1;
  } else state.view = "ask";
  renderShell();
}
function navigate(view) { state.view = view; renderShell(); document.querySelector("#main").focus({ preventScroll: true }); }
async function logout() {
  try { await api("/api/logout", { method: "POST" }); clearTimeout(state.pollTimer); state.currentJob = null; state.question = ""; state.followupParentJob = null; renderAuth(false); }
  catch (error) { toast(errorText(error)); }
}
function renderView() {
  const main = document.querySelector("#main");
  if (state.view === "ask") renderAsk(main);
  else if (state.view === "service") renderServiceCases(main);
  else if (state.view === "documents") renderDocuments(main);
  else if (state.view === "history") renderHistory(main);
  else if (state.view === "users") renderUsers(main);
  else renderSettings(main);
}

function renderAsk(main) {
  const selected = productSelect("question-product");
  selected.addEventListener("change", () => {
    state.selectedProduct = selected.value;
    resetQuestionScope();
    if (state.currentJob && !["queued", "running"].includes(state.currentJob.status)) state.currentJob = null;
    renderJobArea();
    renderReferencePane(document.querySelector("#reference-pane"), null);
    updateProductLinks();
    renderSourceFilters();
  });
  const question = el("textarea", { id: "question", name: "question", required: true, minlength: 2, maxlength: 2000, placeholder: t("questionPlaceholder"), "aria-label": t("questionLabel") }, state.question);
  question.addEventListener("input", () => { state.question = question.value; });
  const aiReady = Boolean(state.model?.ready && state.model?.enabled);
  // AI is the default answer path. Keep the mode choice about the user's intent,
  // not about exposing an implementation detail such as keyword-vs-AI search.
  if (!aiReady) state.useAI = false;
  const productMode = button(ui("제품 문의"), () => { state.inquiryMode = "product"; renderAsk(main); }, `mode-choice ${state.inquiryMode === "product" ? "selected" : ""}`, "book");
  productMode.setAttribute("aria-pressed", state.inquiryMode === "product" ? "true" : "false");
  productMode.setAttribute("title", ui("사양·작동원리·사용방법"));
  const troubleshootingMode = button(ui("고장 상담"), () => { state.inquiryMode = "troubleshooting"; renderAsk(main); }, `mode-choice ${state.inquiryMode === "troubleshooting" ? "selected" : ""}`, "wrench");
  troubleshootingMode.setAttribute("aria-pressed", state.inquiryMode === "troubleshooting" ? "true" : "false");
  troubleshootingMode.setAttribute("title", ui("고장 증상·오류·해결방법"));
  const submit = el("button", { type: "submit", class: "btn", id: "ask-submit" }, icon("arrow"), t("sendQuestion"));
  const form = el("form", { class: "question-form" },
    el("div", { class: "composer" },
      el("div", { class: "composer-top" }, el("label", { for: "question-product" }, ui("제품")), selected),
      el("details", { id: "source-filters", class: "source-filters" }),
      state.followupParentJob && el("div", { class: "followup-context", role: "status" },
        el("div", {}, icon("chat"), el("span", {}, ui("이전 답변에 이어 질문 중: {question}", { question: state.followupParentJob.question }))),
        button(ui("새 질문으로 초기화"), () => { resetQuestionScope(); state.question = ""; renderAsk(main); document.querySelector("#question")?.focus(); }, "ghost compact")),
      question,
      el("div", { class: "composer-bottom" },
        el("fieldset", { class: "mode-control inquiry-mode-control" }, el("legend", { class: "sr-only" }, ui("문의 유형")), productMode, troubleshootingMode), submit)),
    el("p", { class: "form-help" }, state.inquiryMode === "troubleshooting"
      ? ui("고장 증상을 확인하고 근거 있는 해결 방법을 순서대로 안내합니다. Ctrl + Enter로 전송할 수 있습니다.")
      : ui("사양·작동원리·사용방법을 매뉴얼 근거로 설명합니다. Ctrl + Enter로 전송할 수 있습니다.")));
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!has("ask") || !form.reportValidity()) return;
    if (state.currentJob && ["queued", "running"].includes(state.currentJob.status)) { toast(ui("진행 중인 질문이 끝난 후 보내 주세요.")); return; }
    await busy(submit, async () => {
      try {
        state.question = question.value.trim(); state.selectedProduct = selected.value;
        const session = state.sessionVersion; const userId = state.user?.id;
        const job = await api("/api/ask", { method: "POST", body: { product_id: selected.value, question: state.question, use_ai: state.useAI, inquiry_mode: state.inquiryMode, language: state.locale, parent_job_id: state.followupParentJob?.id || "", ...state.sourceFilters } });
        if (session !== state.sessionVersion || userId !== state.user?.id) return;
        clearTranslationTask();
        state.currentJob = { ...job, question: state.question };
        if (job.filters) {
          state.sourceFilters = { ...job.filters };
          if (job.filters.document_id) state.manualSelections[selected.value] = job.filters.document_id;
        }
        state.jobStarted = Date.now(); state.followupParentJob = null;
        document.querySelector(".followup-context")?.remove();
        renderJobArea(); pollJob(job.id);
      } catch (error) {
        document.querySelector("#answer-area").replaceChildren(notice(errorText(error), "error", "alert"));
      }
    }, ui("전송 중…"));
  });
  question.addEventListener("keydown", event => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); form.requestSubmit(); } });
  const intro = el("div", { class: "intro" }, el("div", { class: "eyebrow" }, "PRODUCT MANUAL Q&A"),
    el("h1", {}, t("questionTitle")), el("p", {}, t("questionText"), el("br"), t("questionText2")));
  const content = el("div", { class: "chat-layout answer-focused" }, el("div", { class: "chat-main" }, intro, form, el("div", { id: "answer-area", "aria-live": "polite" })));
  main.replaceChildren(content);
  renderSourceFilters();
  renderJobArea();
  if (!state.currentJob) renderReferencePane(document.querySelector("#reference-pane"), null);
}

function renderServiceCases(main) {
  if (!has("read_service")) { main.replaceChildren(empty(ui("서비스 문서 권한이 없습니다."), ui("관리자에게 서비스 사례 열람 권한을 요청하세요."))); return; }
  const modelSearch = input("service-model-search", "search", { placeholder: ui("서비스 모델 검색"), "aria-label": ui("서비스 모델 검색"), value: state.serviceModelQuery });
  const product = el("select", { id: "service-product", "aria-label": ui("서비스 모델") });
  const refreshModels = () => { const needle = modelSearch.value.trim().toLowerCase(); state.serviceModelQuery = modelSearch.value; const models = state.serviceModels.filter(item => !needle || `${item.id} ${item.name || ""}`.toLowerCase().includes(needle)); product.replaceChildren(el("option", { value: "" }, ui("모든 서비스 모델")), ...models.map(item => el("option", { value: item.id, selected: item.id === state.serviceSelectedModel }, `${item.id} · ${item.count || 0}${ui("건")}`))); if (!models.some(item => item.id === state.serviceSelectedModel)) { state.serviceSelectedModel = ""; product.value = ""; } };
  modelSearch.addEventListener("input", refreshModels); refreshModels();
  const query = el("textarea", { id: "service-case-query", rows: "3", maxlength: "1000", placeholder: ui("예: BT-770 화면이 멈추고 알람이 울리지 않음"), "aria-label": ui("서비스 사례 질문") }, state.serviceCaseQuery);
  const market = el("select", { id: "service-case-market", "aria-label": ui("서비스 처리 지역") },
    el("option", { value: "" }, ui("국내·해외 모두")), el("option", { value: "domestic", selected: state.serviceCaseMarket === "domestic" }, ui("국내 서비스")), el("option", { value: "overseas", selected: state.serviceCaseMarket === "overseas" }, ui("해외 서비스")));
  const sort = el("select", { id: "service-case-sort", "aria-label": ui("유사 사례 정렬") },
    el("option", { value: "newest", selected: state.serviceCaseSort === "newest" }, ui("최신 사례부터")),
    el("option", { value: "oldest", selected: state.serviceCaseSort === "oldest" }, ui("오래된 사례부터")),
    el("option", { value: "similarity", selected: state.serviceCaseSort === "similarity" }, ui("유사도순")));
  const dateField = el("select", { id: "service-case-date-field", "aria-label": ui("날짜 기준") },
    el("option", { value: "received", selected: state.serviceCaseDateField === "received" }, ui("접수일 기준")),
    el("option", { value: "completed", selected: state.serviceCaseDateField === "completed" }, ui("완료일 기준")));
  const submit = button(ui("유사 사례 찾기"), async () => {
    state.serviceCaseQuery = query.value.trim(); state.serviceSelectedModel = product.value; state.serviceCaseMarket = market.value; state.serviceCaseSort = sort.value; state.serviceCaseDateField = dateField.value; state.serviceCasePage = 1; state.serviceCaseTotal = 0; state.serviceCaseHasMore = false; state.serviceCases = []; state.serviceCaseAnswer = ""; await searchServiceCases(submit, 1, { summarize: true });
  }, "", "search");
  const resetSort = async (event, field) => {
    state[field] = event.currentTarget.value; state.serviceCasePage = 1; state.serviceCaseTotal = 0; state.serviceCaseHasMore = false; state.serviceCases = []; state.serviceCaseAnswer = "";
    if (state.serviceCaseQuery) await searchServiceCases(submit, 1, { summarize: false });
  };
  sort.addEventListener("change", event => { void resetSort(event, "serviceCaseSort"); });
  dateField.addEventListener("change", event => { void resetSort(event, "serviceCaseDateField"); });
  const importButton = has("manage_documents") ? button(ui("엑셀 가져오기"), showServiceImport, "secondary compact", "upload") : null;
  const form = el("div", { class: "service-case-form" }, field(ui("서비스 모델 검색"), modelSearch), field(ui("서비스 모델"), product), field(ui("증상·오류·처리 내용"), query), field(ui("처리 지역"), market), field(ui("정렬"), sort), field(ui("날짜 기준"), dateField), el("div", { class: "actions" }, submit, importButton));
  const results = el("div", { id: "service-case-results", class: "service-case-results" });
  const guidePanel = el("div", { id: "service-guide-panel", class: "service-guide-panel" });
  const clearSearchState = () => { state.serviceCasePage = 1; state.serviceCaseTotal = 0; state.serviceCaseHasMore = false; state.serviceCases = []; state.serviceCaseAnswer = ""; state.serviceCaseRequest++; state.serviceCaseBusy = false; submit.disabled = false; renderServiceCaseResults(results); };
  query.addEventListener("input", clearSearchState); product.addEventListener("change", clearSearchState); market.addEventListener("change", clearSearchState);
  main.replaceChildren(...[pageHeading(ui("서비스 처리 사례"), ui("매뉴얼 답변과 별도로 기존 국내·해외 처리 이력에서 유사 사례를 확인합니다.")), state.serviceSources.length ? el("p", { class: "small muted" }, ui("등록된 자료: {count}개", { count: number(state.serviceSources.reduce((sum, source) => sum + Number(source.record_count || 0), 0)) })) : null, form, guidePanel, results].filter(Boolean));
  renderServiceCaseResults(results);
  renderServiceGuidePanel(guidePanel);
  if (!state.serviceRegistryLoaded) loadServiceRegistry();
  if (!state.serviceGuidesLoaded) loadServiceGuides();
}
async function loadServiceRegistry() {
  const session = state.sessionVersion;
  try { const [models, sources] = await Promise.all([api("/api/service-history/models"), api("/api/service-history/sources")]); if (session !== state.sessionVersion) return; state.serviceModels = models || []; state.serviceSources = sources || []; state.serviceRegistryLoaded = true; if (state.view === "service") renderServiceCases(document.querySelector("#main")); }
  catch (error) { if (session === state.sessionVersion) { state.serviceRegistryLoaded = true; toast(errorText(error)); } }
}
function showServiceImport() {
  const source = el("select", { id: "service-import-source", required: true }, el("option", { value: "" }, ui("자료 구분 선택")), el("option", { value: "domestic" }, ui("국내 서비스")), el("option", { value: "overseas" }, ui("해외 서비스")));
  const file = input("service-import-file", "file", { required: true, accept: ".xlsx" });
  const submit = el("button", { type: "submit", class: "btn" }, ui("서비스 이력 가져오기"));
  const form = el("form", { class: "form-stack" }, notice(ui("원본 엑셀은 보존하고 로컬 검색용 사본만 등록합니다. 연락처와 고객 식별 정보는 검색에서 제외합니다."), "warning"), field(ui("자료 구분"), source), field(ui("엑셀 파일"), file), el("div", { class: "dialog-footer" }, submit));
  form.addEventListener("submit", async event => { event.preventDefault(); if (!form.reportValidity()) return; await busy(submit, async () => { try { const data = new FormData(); data.append("source", source.value); data.append("file", file.files[0]); const result = await api("/api/service-history/import", { method: "POST", body: data }); dialog.close(); state.serviceCases = []; state.serviceCaseAnswer = ""; state.serviceCasePage = 1; state.serviceCaseTotal = 0; state.serviceCaseHasMore = false; state.serviceCaseRequest++; await loadServiceRegistry(); toast(ui("서비스 이력을 등록했습니다. {count}건", { count: number(result.record_count || 0) })); } catch (error) { dialogError(form, error); } }, ui("엑셀을 분석·등록 중…")); });
  showDialog(ui("서비스 이력 엑셀 가져오기"), form);
}
async function searchServiceCases(control, page = state.serviceCasePage, options = {}) {
  if (state.serviceCaseBusy || !state.serviceCaseQuery) { if (!state.serviceCaseQuery) toast(ui("증상이나 오류를 입력하세요.")); return; }
  state.serviceCaseBusy = true; state.serviceCasePage = page; const session = state.sessionVersion; const request = ++state.serviceCaseRequest; control.disabled = true; renderServiceCaseResults(document.querySelector("#service-case-results"));
  try {
    const body = { query: state.serviceCaseQuery, model: state.serviceSelectedModel || "", source: state.serviceCaseMarket || "", language: state.locale, sort: state.serviceCaseSort, date_field: state.serviceCaseDateField, page, page_size: state.serviceCasePageSize };
    const result = await api(options.summarize === true ? "/api/service-history/question" : "/api/service-history/browse", { method: "POST", body });
    if (session !== state.sessionVersion || request !== state.serviceCaseRequest) return;
    state.serviceCases = Array.isArray(result) ? result : (result.cases || result.items || []); state.serviceCaseAnswer = options.summarize === true ? (result.answer || result.message || "") : ""; state.serviceCaseTotal = Number(result.total || state.serviceCases.length || 0); state.serviceCaseHasMore = Boolean(result.has_more || state.serviceCasePage * state.serviceCasePageSize < state.serviceCaseTotal); renderServiceCaseResults(document.querySelector("#service-case-results"));
  } catch (error) { if (session === state.sessionVersion && request === state.serviceCaseRequest) { state.serviceCases = []; state.serviceCaseAnswer = ""; state.serviceCaseTotal = 0; state.serviceCaseHasMore = false; toast(errorText(error)); } }
  finally { if (request !== state.serviceCaseRequest) return; state.serviceCaseBusy = false; if (control.isConnected) control.disabled = false; if (session === state.sessionVersion && state.view === "service") renderServiceCaseResults(document.querySelector("#service-case-results")); }
}
function renderServiceCaseResults(container) {
  if (!container) return;
  if (state.serviceCaseBusy) { container.replaceChildren(notice(ui("서비스 처리 사례를 검색하는 중입니다."))); return; }
  if (!state.serviceCaseQuery) { container.replaceChildren(el("p", { class: "muted" }, ui("질문을 입력하면 유사 처리 사례를 최대 5건 보여줍니다."))); return; }
  if (!state.serviceCases.length) {
    const fallback = state.serviceCasePage > 1 ? button(ui("이전 페이지"), () => { void searchServiceCases(container, Math.max(1, state.serviceCasePage - 1), { summarize: false }); }, "secondary compact") : null;
    container.replaceChildren(empty(ui("유사한 처리 사례가 없습니다."), ui("제품 모델과 증상·오류 코드를 구체적으로 입력해 보세요."), fallback, "search")); return;
  }
  const dateKey = state.serviceCaseDateField === "completed" ? "completed_date" : "case_date";
  const dateLabel = state.serviceCaseDateField === "completed" ? ui("완료일") : ui("접수일");
  const missingDate = ui("날짜 미확인");
  const pages = Math.max(1, Math.ceil(state.serviceCaseTotal / state.serviceCasePageSize));
  const pager = el("div", { class: "service-case-pager", "aria-label": ui("서비스 사례 페이지") },
    button(ui("이전"), () => { void searchServiceCases(container, state.serviceCasePage - 1, { summarize: false }); }, "secondary compact"),
    el("span", { class: "small muted" }, ui("{page} / {pages}페이지 · 전체 {count}건", { page: number(state.serviceCasePage), pages: number(pages), count: number(state.serviceCaseTotal) })),
    button(ui("다음"), () => { void searchServiceCases(container, state.serviceCasePage + 1, { summarize: false }); }, "secondary compact"));
  const previous = pager.children[0]; const next = pager.children[2];
  if (previous) previous.disabled = state.serviceCasePage <= 1;
  if (next) next.disabled = !state.serviceCaseHasMore && state.serviceCasePage >= pages;
  container.replaceChildren(state.serviceCaseAnswer && el("div", { class: "service-case-answer" }, el("h2", {}, ui("서비스 사례 답변")), el("p", {}, state.serviceCaseAnswer)), el("h2", {}, ui("유사 서비스 처리 사례 {count}건", { count: number(state.serviceCaseTotal || state.serviceCases.length) })), el("p", { class: "small muted" }, ui("한 페이지에 최대 {count}건씩 표시합니다.", { count: number(state.serviceCasePageSize) })), el("p", { class: "small muted" }, ui("과거 처리 이력은 참고 사례이며 현재 고장 원인을 확정하지 않습니다.")), ...state.serviceCases.map((item, index) => {
    const marketLabel = item.source === "domestic" ? ui("국내 서비스") : item.source === "overseas" ? ui("해외 서비스") : ui("지역 미확인");
    const detail = el("details", { class: "service-case-card" }, el("summary", {}, el("strong", {}, `${item.model || ui("제품 미확인")} · ${dateLabel}: ${item[dateKey] || missingDate}`), badge(item.relevance === "high" ? ui("유사도 높음") : ui("유사도 보통"), "accent"), el("p", { class: "small muted" }, `${marketLabel} · ${(item.symptom || ui("증상 미확인")).slice(0, 120)}`)),
      el("dl", {}, item.symptom && el("div", {}, el("dt", {}, ui("증상")), el("dd", {}, item.symptom)), item.cause && el("div", {}, el("dt", {}, ui("확인 내용")), el("dd", {}, item.cause)), item.action && el("div", {}, el("dt", {}, ui("조치")), el("dd", {}, item.action)), item.result && el("div", {}, el("dt", {}, ui("처리 결과")), el("dd", {}, item.result)), item.match_reason && el("div", {}, el("dt", {}, ui("유사 이유")), el("dd", {}, item.match_reason))),
      item.filename && button(ui("처리 내역 원문 보기"), () => showServiceCaseDetails(item), "secondary compact"),
      button(ui("이 사례로 해결 가이드 시작"), () => { void startServiceGuide(item, "actual"); }, "compact", "wrench"),
      button(ui("연습 시뮬레이션"), () => { void startServiceGuide(item, "practice"); }, "secondary compact", "search"));
    return detail;
  }), pager);
}
function guideStatusLabel(status, mode = "actual") {
  const labels = { active: ui("진행 중"), paused: ui("일시 중지"), resolved: mode === "practice" ? ui("해결 가정 완료") : ui("사용자 확인 완료"), exhausted: ui("추가 근거 없음"), professional_review: ui("전문가 확인 필요"), stopped: ui("종료됨"), source_changed: ui("근거 변경됨") };
  return labels[status] || status || ui("상태 미확인");
}
function guideModeLabel(mode) { return mode === "practice" ? ui("연습 시뮬레이션") : ui("실제 모드"); }
function normalServiceModel(value) { return String(value || "").toLowerCase().replace(/[^a-z0-9가-힣]/g, ""); }
function guideActionLabel(action, mode = "actual") {
  const labels = { resolved: mode === "practice" ? ui("해결된 것으로 가정") : ui("해결됨"), unresolved: mode === "practice" ? ui("미해결로 가정") : ui("미해결"), unable: ui("실행 불가"), symptom_changed: ui("증상 변경"), pause: ui("일시 중지"), resume: ui("계속 진행"), stop: ui("종료") };
  return labels[action] || action;
}
function guidePanelTarget() { return document.querySelector("#service-guide-panel"); }
function renderServiceGuidePanel(container = guidePanelTarget()) {
  if (!container) return;
  const listOpen = Boolean(container.querySelector(".service-guide-list")?.open);
  const list = (state.serviceGuides || []).filter(item => state.serviceGuideModeFilter === "all" || (item.mode || "actual") === state.serviceGuideModeFilter);
  const guide = state.serviceGuide;
  const status = guide?.status;
  const terminal = ["resolved", "exhausted", "professional_review", "stopped", "source_changed"].includes(status);
  const modeFilter = el("select", { id: "service-guide-mode-filter", "aria-label": ui("가이드 모드 필터") }, el("option", { value: "all", selected: state.serviceGuideModeFilter === "all" }, ui("전체 모드")), el("option", { value: "actual", selected: state.serviceGuideModeFilter === "actual" }, ui("실제 모드")), el("option", { value: "practice", selected: state.serviceGuideModeFilter === "practice" }, ui("연습 시뮬레이션")));
  modeFilter.addEventListener("change", event => { state.serviceGuideModeFilter = event.currentTarget.value; state.serviceGuideListPage = 1; renderServiceGuidePanel(container); });
  const visibleGuides = list.slice(0, state.serviceGuideListPage * 5);
  const guideRows = visibleGuides.map(item => el("div", { class: "service-guide-list-item" }, el("span", {}, `${item.model || ui("제품 미확인")} · ${guideModeLabel(item.mode)} · ${guideStatusLabel(item.status, item.mode)}`), button(ui("열기"), () => { void openServiceGuide(item.id); }, "secondary compact")));
  if (list.length > visibleGuides.length) guideRows.push(button(ui("가이드 5건 더 보기"), () => { state.serviceGuideListPage++; renderServiceGuidePanel(container); }, "secondary compact"));
  const summary = el("details", { class: "service-guide-list", open: listOpen }, el("summary", { class: "service-guide-list-header" }, ui("내 해결 가이드 {count}건", { count: number(list.length) })), modeFilter, guideRows.length ? guideRows : el("p", { class: "small muted" }, ui("해당 모드의 가이드가 없습니다.")));
  if (!guide) { container.replaceChildren(summary || el("p", { class: "small muted" }, ui("서비스 사례에서 해결 가이드를 시작할 수 있습니다."))); return; }
  const step = guide.current_step;
  const sourceItem = step ? { case_id: step.case_id, model: step.model || guide.model, symptom: step.symptom, cause: step.cause, action: step.action, result: step.result, filename: step.filename, sheet: step.sheet, row_number: step.row_number } : null;
  const history = Array.isArray(guide.history) ? guide.history : [];
  const note = el("textarea", { id: "service-guide-note", rows: "2", maxlength: "1000", placeholder: ui("확인 내용을 입력하세요 (선택)") });
  const newSymptom = el("textarea", { id: "service-guide-new-symptom", rows: "2", maxlength: "1000", hidden: true, placeholder: ui("변경된 증상을 입력하세요") });
  const newSymptomField = field(ui("변경된 증상"), newSymptom); newSymptomField.hidden = true;
  const symptomSave = button(ui("증상 변경 저장"), () => { void sendServiceGuideEvent("symptom_changed"); }, "secondary compact"); symptomSave.hidden = true;
  const actions = ["resolved", "unresolved", "unable", "symptom_changed", "pause", "stop"].map(action => button(guideActionLabel(action, guide.mode), () => { if (action === "symptom_changed") { newSymptom.hidden = false; newSymptomField.hidden = false; symptomSave.hidden = false; newSymptom.focus(); } else void sendServiceGuideEvent(action); }, action === "stop" ? "secondary compact" : "compact"));
  const current = step && !terminal && status !== "paused" ? el("div", { class: "service-guide-step" },
    el("p", { class: "small muted" }, guide.mode === "practice" ? ui("연습 시뮬레이션입니다. 실제 장비를 조작하지 않으며 결과는 가정으로만 기록됩니다.") : ui("실제 장비를 자동 조작하지 않습니다. 사용자가 확인한 결과만 기록합니다.")),
    el("h3", {}, step.action || ui("현재 확인 단계")),
    guide.mode === "practice" && step.risk === "professional" ? notice(ui("전문가 조치의 과거 기록만 표시합니다. 구체적인 조작 절차는 연습에 추가하지 않습니다."), "warning") : el("p", {}, step.instruction || ui("현재 근거에서 확인할 수 있는 절차가 없습니다.")),
    step.risk && notice(step.risk === "professional" ? ui("전문가 확인이 필요한 단계입니다.") : step.risk === "unverified" ? ui("근거가 확인되지 않은 절차는 실행하지 마세요.") : ui("사용자 확인 단계입니다."), step.risk === "user_check" ? "" : "warning"),
    sourceItem && button(ui("근거 보기"), () => { void showServiceCaseDetails(sourceItem); }, "secondary compact"),
    field(ui("확인 메모"), note), newSymptomField, symptomSave,
    el("div", { class: "service-guide-actions" }, ...actions)) : null;
  if (guide.current_step) { newSymptom.hidden = true; newSymptomField.hidden = true; }
  const pausedActions = status === "paused" ? button(ui("종료"), () => { void sendServiceGuideEvent("stop"); }, "secondary compact") : null;
  const statusNotice = status === "resolved" ? notice(ui(guide.mode === "practice" ? "해결된 것으로 가정했습니다." : "사용자가 해결됨으로 확인했습니다."), "success") : status === "professional_review" ? notice(ui("전문가 확인이 필요하여 자동 안내를 중지했습니다."), "warning") : status === "source_changed" ? notice(ui("근거 자료가 변경되어 새 근거를 확인해야 합니다."), "warning") : status === "exhausted" ? notice(ui("추가로 확인할 수 있는 근거가 없어 안내를 종료했습니다."), "warning") : status === "stopped" ? notice(ui("해결 가이드를 종료했습니다."), "warning") : guide.message ? notice(guide.message) : null;
  const historyView = history.length ? el("details", { class: "service-guide-history" }, el("summary", {}, ui("진행 기록 {count}건", { count: number(history.length) })), ...history.map(item => el("p", { class: "small" }, `${guideActionLabel(item.action, guide.mode)} · ${item.note || ui("메모 없음")} · ${item.at || ""}`))) : null;
  const resume = status === "paused" ? button(ui("계속 진행"), () => { void sendServiceGuideEvent("resume"); }, "compact") : null;
  container.replaceChildren(summary, el("div", { class: "service-guide-current" }, el("div", { class: "service-guide-header" }, el("h2", {}, ui("해결 가이드")), el("div", { class: "service-guide-badges" }, badge(guideModeLabel(guide.mode), guide.mode === "practice" ? "warning" : "accent"), badge(guideStatusLabel(status, guide.mode), status === "resolved" ? "success" : "accent"))), guide.mode === "practice" ? notice(ui("연습 모드: 실제 장비 조작 없음 · 결과는 가정으로 기록"), "warning") : null, el("p", { class: "small muted" }, `${guide.model || ui("제품 미확인")} · ${ui("시도 {count}회", { count: number(guide.attempted_count || 0) })}`), statusNotice, resume, pausedActions, current, historyView));
}
async function loadServiceGuides() {
  if (!has("read_service") || state.serviceGuidesLoaded) return;
  const session = state.sessionVersion; const request = ++state.serviceGuideListRequest;
  try { const result = await api("/api/service-history/guides"); if (session !== state.sessionVersion || request !== state.serviceGuideListRequest) return; state.serviceGuides = Array.isArray(result) ? result : (result.items || []); state.serviceGuidesLoaded = true; renderServiceGuidePanel(); }
  catch (error) { if (session === state.sessionVersion) { state.serviceGuidesLoaded = true; toast(errorText(error)); } }
}
async function openServiceGuide(id) {
  if (!id || !has("read_service")) return;
  const session = state.sessionVersion; const request = ++state.serviceGuideRequest; state.serviceGuideBusy = true; renderServiceGuidePanel();
  try { const result = await api(`/api/service-history/guides/${encodeURIComponent(id)}`); if (session !== state.sessionVersion || request !== state.serviceGuideRequest) return; state.serviceGuide = result; renderServiceGuidePanel(); }
  catch (error) { if (session === state.sessionVersion && request === state.serviceGuideRequest) toast(errorText(error)); }
  finally { if (session === state.sessionVersion && request === state.serviceGuideRequest) { state.serviceGuideBusy = false; renderServiceGuidePanel(); } }
}
async function startServiceGuide(item, mode = "actual") {
  if (!has("read_service")) return;
  const selected = state.serviceSelectedModel || "";
  if (!selected) { toast(ui("서비스 모델을 먼저 선택하세요.")); return; }
  if (item.model && normalServiceModel(selected) !== normalServiceModel(item.model)) { toast(ui("사례의 제품 모델과 선택한 모델이 다릅니다.")); return; }
  const session = state.sessionVersion; const request = ++state.serviceGuideRequest; state.serviceGuideBusy = true; renderServiceGuidePanel();
  try { const result = await api("/api/service-history/guides", { method: "POST", body: { query: state.serviceCaseQuery, model: selected, source: state.serviceCaseMarket || "", case_id: item.case_id, mode: mode === "practice" ? "practice" : "actual", language: state.locale } }); if (session !== state.sessionVersion || request !== state.serviceGuideRequest) return; state.serviceGuide = result; state.serviceGuidesLoaded = false; await loadServiceGuides(); renderServiceGuidePanel(); }
  catch (error) { if (session === state.sessionVersion && request === state.serviceGuideRequest) toast(errorText(error)); }
  finally { if (session === state.sessionVersion && request === state.serviceGuideRequest) { state.serviceGuideBusy = false; renderServiceGuidePanel(); } }
}
async function sendServiceGuideEvent(action) {
  const guide = state.serviceGuide; if (!guide || state.serviceGuideBusy) return;
  const noteControl = document.querySelector("#service-guide-note"); const symptomControl = document.querySelector("#service-guide-new-symptom");
  const note = action === "symptom_changed" ? symptomControl?.value.trim() || "" : noteControl?.value.trim() || "";
  if (action === "symptom_changed" && !note) { toast(ui("변경된 증상을 입력하세요.")); return; }
  const session = state.sessionVersion; const request = ++state.serviceGuideRequest; state.serviceGuideBusy = true; renderServiceGuidePanel();
  try { const result = await api(`/api/service-history/guides/${encodeURIComponent(guide.id)}/events`, { method: "POST", body: { revision: Number(guide.revision || 0), action, note } }); if (session !== state.sessionVersion || request !== state.serviceGuideRequest) return; state.serviceGuide = result; state.serviceGuidesLoaded = false; await loadServiceGuides(); renderServiceGuidePanel(); }
  catch (error) { if (session === state.sessionVersion && request === state.serviceGuideRequest) toast(errorText(error)); }
  finally { if (session === state.sessionVersion && request === state.serviceGuideRequest) { state.serviceGuideBusy = false; renderServiceGuidePanel(); } }
}
async function showServiceCaseDetails(item) {
  const body = el("div", {}, el("p", { class: "muted" }, ui("처리 내역 원문을 확인하는 중입니다."))); showDialog(ui("서비스 처리 사례"), body);
  const session = state.sessionVersion;
  try {
    const result = await api(`/api/service-history/case/${encodeURIComponent(item.case_id)}`);
    if (session !== state.sessionVersion || !dialog.open) return;
    const source = result.source || item.source || "";
    const sourceLabel = source === "domestic" ? ui("국내 서비스") : source === "overseas" ? ui("해외 서비스") : ui("지역 미확인");
    const sections = [
      [ui("증상"), result.symptom], [ui("확인 내용"), result.cause], [ui("조치"), result.action], [ui("처리 결과"), result.result],
    ].filter(([, value]) => value).map(([label, value]) => el("div", {}, el("dt", {}, label), el("dd", {}, value)));
    const provenance = el("div", { class: "service-case-provenance" },
      el("p", { class: "small muted" }, ui("출처: {source}", { source: result.filename || item.filename || ui("미확인") })),
      el("p", { class: "small muted" }, `${sourceLabel} · ${result.sheet || item.sheet || ui("시트 미확인")} · ${ui("행 {row}", { row: result.row_number || item.row_number || ui("미확인") })}`),
      has("manage_documents") && source && link(ui("원본 업무대장 열기"), `/api/service-history/source/${encodeURIComponent(source)}/original`, "btn secondary compact"));
    body.replaceChildren(el("h3", {}, result.model || item.model || ui("제품 미확인")), el("dl", { class: "service-case-detail" }, ...sections), provenance);
  }
  catch (error) { if (dialog.open) body.replaceChildren(notice(errorText(error), "error", "alert")); }
}
function updateProductLinks() {
  for (const item of document.querySelectorAll(".product-link")) {
    const selected = item.getAttribute("data-product-id") === state.selectedProduct;
    item.classList.toggle("selected", selected); item.setAttribute("aria-pressed", String(selected));
  }
  const label = document.querySelector("#selected-product-label");
  if (label) label.textContent = state.selectedProduct ? ui("선택 제품: {product}", { product: state.selectedProduct }) : ui("제품군을 펼쳐 세부 모델을 선택하세요.");
}
function renderJobArea() {
  if (state.view !== "ask") return;
  const area = document.querySelector("#answer-area");
  const job = state.currentJob;
  const submit = document.querySelector("#ask-submit");
  if (submit) submit.disabled = Boolean(job && ["queued", "running"].includes(job.status)) || translationActive();
  if (!job) {
    const published = state.documents.filter(item => item.state === "published");
    area.replaceChildren(published.length ? empty(ui("매뉴얼에서 시작하는 질문"), ui("정확한 제품명과 표시된 알람 문구를 함께 적으면 관련 내용을 더 쉽게 찾을 수 있습니다."), null, "search") :
      empty(ui("아직 게시된 매뉴얼이 없습니다"), has("manage_documents") ? ui("자료실에서 매뉴얼을 등록하고 원문을 검토한 뒤 게시하세요.") : ui("관리자가 매뉴얼을 게시하면 제품 질문을 시작할 수 있습니다."),
        has("manage_documents") ? button(ui("자료실 열기"), () => navigate("documents"), "secondary", "book") : null));
    return;
  }
  if (["queued", "running"].includes(job.status)) {
    const elapsed = Math.max(0, Math.floor((Date.now() - state.jobStarted) / 1000));
    area.replaceChildren(el("div", { class: "job-status", role: "status" },
      el("span", { class: "spinner", "aria-hidden": "true" }),
      el("div", {}, el("strong", {}, job.status === "queued" ? ui("질문을 기다리고 있습니다") : ui("매뉴얼의 근거를 확인하고 있습니다")),
        el("p", { class: "muted small" },
          job.status === "queued" && job.queue_position ? `${ui("대기 순서 {position}", { position: number(job.queue_position) })} · ` : "",
          ui("{seconds}초 경과", { seconds: number(elapsed) }))),
      button(ui("취소"), async event => {
        await busy(event.currentTarget, async () => {
          try { state.currentJob = await api(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, { method: "POST" }); renderJobArea(); }
          catch (error) { toast(errorText(error)); }
        });
      }, "secondary compact")));
    renderReferencePane(document.querySelector("#reference-pane"), null); return;
  }
  if (job.status === "failed" || job.status === "cancelled") {
    area.replaceChildren(notice(job.status === "cancelled" ? ui("질문을 취소했습니다. 내용을 바꾸어 다시 보낼 수 있습니다.") : (job.error ? ui(job.error) : null) || ui("답변을 만들지 못했습니다. 근거 검색으로 다시 시도해 주세요."), job.status === "failed" ? "error" : ""));
    renderReferencePane(document.querySelector("#reference-pane"), null); return;
  }
  if (job.result) {
    area.replaceChildren(renderAnswer(job.question, job.result, job));
    renderReferencePane(document.querySelector("#reference-pane"), job.result);
    ensureAnswerTranslation();
  }
}
async function pollJob(id) {
  clearTimeout(state.pollTimer);
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
    if (state.currentJob?.id !== id) return;
    state.currentJob = job; renderJobArea();
    if (["queued", "running"].includes(job.status)) state.pollTimer = setTimeout(() => pollJob(id), 1200);
  } catch (error) {
    if (state.currentJob?.id !== id) return;
    if (error.status === 401) { renderAuth(false); return; }
    state.currentJob = { ...state.currentJob, status: "failed", error: errorText(error) }; renderJobArea();
  }
}
function citationNodes(ids, evidence) {
  return el("span", { class: "citations" }, ids.map(id => {
    const index = evidence.findIndex(item => item.evidence_id === id);
    if (index < 0) return null;
    const source = evidence[index];
    return link(`[${index + 1}]`, docUrl(source.document_id, source.physical_page), "citation");
  }));
}
function answerTextKeys(result) {
  return [
    ...(result.claims || []).map(claim => `claim:${claim.id}`),
    ...(result.conflicts || []).flatMap((conflict, index) => [
      `conflict:${index}:topic`, ...(conflict.variants || []).map((_, variant) => `conflict:${index}:variant:${variant}`),
    ]),
    ...(result.questions || []).map((_, index) => `question:${index}`),
  ];
}
function hasAnswerTranslation(result) {
  return result?.translation?.language === state.locale && answerTextKeys(result).every(key =>
    typeof result.translation.texts?.[key] === "string" && result.translation.texts[key].trim());
}
function answerText(result, key, original, translated = state.locale === "en") {
  return translated && hasAnswerTranslation(result) ? result.translation.texts[key] : original;
}
function answerMessage(message, translated = false) {
  const validitySuffix = "원본의 최신·유효 상태와 최종 승인 여부는 확인되지 않았습니다.";
  if (message.endsWith(validitySuffix)) {
    const original = message.slice(0, -validitySuffix.length).trim();
    return [original && answerMessage(original, translated), ui(validitySuffix)].filter(Boolean).join(" ");
  }
  const prefix = "쉬운 설명을 채택하지 않았습니다. ";
  const suffix = " 선택한 원문을 표시합니다.";
  if (state.locale === "ko" && translated && message.endsWith(suffix)) {
    return ui("{reason} 번역된 답변을 표시합니다. 원문은 아래에서 확인할 수 있습니다.", {
      reason: message.slice(0, -suffix.length).trim(),
    });
  }
  if (state.locale === "ko" && /한국어 번역을 완료하지 못했습니다/.test(message)) {
    return ui("한국어 번역을 완료하지 못했습니다. 원문 답변 보기에서 확인할 수 있습니다.");
  }
  if (state.locale === "en" && message.startsWith(prefix) && message.endsWith(suffix)) {
    const reason = message.slice(prefix.length, -suffix.length);
    const translated = ui(reason);
    if (translated !== reason) {
      return ui("쉬운 설명을 채택하지 않았습니다. {reason} 선택한 원문을 표시합니다.", { reason: translated });
    }
  }
  return ui(message);
}
function displayedAnswerTexts(result) {
  return [
    ...directClaims(result).map(item => item.text),
    ...(result?.questions || []),
  ].filter(item => typeof item === "string" && item.trim());
}
function answerNeedsTranslation(result, language = state.locale) {
  if (!result) return false;
  // Only text shown in the normal answer is eligible for automatic translation.
  // Source claims and conflict variants remain available through the manual
  // original-view dialog and must not trigger a hidden GPU translation job.
  const texts = displayedAnswerTexts(result);
  if (!texts.length) return false;
  // Translate only when the answer contains the other script. Product IDs
  // such as BT-500 do not count as English prose by themselves.
  if (language !== "ko") return texts.some(item => /[가-힣]/.test(item));
  return texts.some(item => {
    const asciiWords = item.match(/[A-Za-z]{3,}/g) || [];
    const hangulWords = item.match(/[가-힣]+/g) || [];
    if (!asciiWords.length) return false;
    if (!hangulWords.length) return true;
    return asciiWords.length >= 2 && asciiWords.length >= hangulWords.length;
  });
}
function clearTranslationTask() {
  clearTimeout(state.translationTimer);
  state.translationTimer = null; state.translationTask = null;
}
function translationTaskOwned(task) {
  return state.translationTask === task && task.session === state.sessionVersion &&
    task.userId === state.user?.id && task.sourceId === state.currentJob?.id;
}
function translationTaskCurrent(task) {
  return translationTaskOwned(task) && (!task.target || task.target === state.locale);
}
function currentTranslationTask() {
  const task = state.translationTask;
  if (!task || !translationTaskOwned(task)) return null;
  if (["requesting", "queued", "running", "checking"].includes(task.status)) return task;
  return !task.target || task.target === state.locale ? task : null;
}
function translationActive() {
  const task = currentTranslationTask();
  return Boolean(task && ["requesting", "queued", "running", "checking"].includes(task.status));
}
function ensureAnswerTranslation() {
  const job = state.currentJob;
  const cachedTask = state.translationTask;
  if (cachedTask?.result && cachedTask.target === state.locale && translationTaskOwned(cachedTask)) {
    state.currentJob = { ...job, result: cachedTask.result };
    cachedTask.result = null;
    renderJobArea();
    return;
  }
  if (!state.user || state.view !== "ask" || job?.status !== "completed" ||
      !job.result || !answerTextKeys(job.result).length || hasAnswerTranslation(job.result) ||
      !answerNeedsTranslation(job.result) || job.result.translation_error || currentTranslationTask()) return;
  void requestAnswerTranslation();
}
async function requestAnswerTranslation() {
  const source = state.currentJob;
  if (!state.user || source?.status !== "completed" || !source.result || translationActive()) return;
  clearTranslationTask();
  const task = {
    sourceId: source.id, userId: state.user.id, session: state.sessionVersion,
    target: state.locale,
    status: "requesting", job: null, error: null, started: Date.now(),
  };
  state.translationTask = task;
  renderJobArea();
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(source.id)}/translate`, { method: "POST", body: { language: state.locale } });
    if (!translationTaskOwned(task)) return;
    await acceptTranslationJob(task, job);
  } catch (error) {
    if (!translationTaskOwned(task)) return;
    if (error.status === 401) { renderAuth(false); return; }
    task.status = "failed"; task.error = error;
    if (error.status) await checkTranslationSource(task);
    if (translationTaskCurrent(task)) renderJobArea();
  }
}
async function checkTranslationSource(task) {
  if (task.target && task.target !== state.locale) return;
  task.status = "checking"; renderJobArea();
  try {
    const source = await api(`/api/jobs/${encodeURIComponent(task.job?.source_job_id || task.sourceId)}`);
    if (!translationTaskOwned(task)) return;
    task.status = "failed";
    state.currentJob = source.status === "completed" && source.result ?
      { ...state.currentJob, status: "completed", question: source.question, result: source.result, error: "" } :
      { ...state.currentJob, status: "failed", question: source.question, result: null,
        error: source.error || task.job?.error || errorText(task.error) };
  } catch (error) {
    if (!translationTaskOwned(task)) return;
    if (error.status === 401) { renderAuth(false); return; }
    task.status = "failed"; task.error = error;
    state.currentJob = { ...state.currentJob, status: "failed", question: "", result: null, error: errorText(error) };
  }
}
async function acceptTranslationJob(task, job) {
  if (!translationTaskOwned(task)) return;
  clearTimeout(state.translationTimer);
  task.job = job; task.status = job.status;
  if (task.target && task.target !== state.locale) {
    if (job.status === "completed" && job.result) task.result = job.result;
    if (["queued", "running"].includes(job.status)) {
      state.translationTimer = setTimeout(() => pollTranslation(task), 1200);
    }
    return;
  }
  if (job.status === "completed" && job.result) {
    state.currentJob = { ...state.currentJob, result: job.result };
  } else if (job.status === "failed") {
    // A translation can fail independently of its source (for example, queue timeout).
    await checkTranslationSource(task);
    if (!translationTaskOwned(task)) return;
  }
  renderJobArea();
  if (["queued", "running"].includes(job.status)) {
    state.translationTimer = setTimeout(() => pollTranslation(task), 1200);
  }
}
async function pollTranslation(task) {
  if (!translationTaskOwned(task) || !task.job) return;
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(task.job.id)}`);
    if (translationTaskOwned(task)) await acceptTranslationJob(task, job);
  } catch (error) {
    if (!translationTaskOwned(task)) return;
    if (error.status === 401) { renderAuth(false); return; }
    task.status = "failed"; task.error = error;
    if (error.status) await checkTranslationSource(task);
    if (translationTaskCurrent(task)) renderJobArea();
  }
}
function translationNotice(result, options = {}) {
  if (!answerNeedsTranslation(result) || !answerTextKeys(result).length) return null;
  if (hasAnswerTranslation(result)) return el("p", { class: "translation-note small muted" }, t(state.locale === "ko" ? "answerTranslatedKorean" : "answerTranslated"));
  const task = currentTranslationTask();
  if (translationActive()) {
    const elapsed = Math.max(0, Math.floor((Date.now() - task.started) / 1000));
    const cancel = task.job && ["queued", "running"].includes(task.job.status) && button(t("cancelTranslation"), async event => {
      await busy(event.currentTarget, async () => {
        try {
          const job = await api(`/api/jobs/${encodeURIComponent(task.job.id)}/cancel`, { method: "POST" });
          if (translationTaskOwned(task)) await acceptTranslationJob(task, job);
        } catch (error) { if (translationTaskOwned(task)) toast(errorText(error)); }
      });
    }, "secondary compact");
    return el("div", { class: "job-status translation-status", role: "status" },
      el("span", { class: "spinner", "aria-hidden": "true" }),
      el("div", {}, el("strong", {}, task.status === "checking" ? t("checkingOriginalAnswer") :
        task.status === "queued" ? t(state.locale === "ko" ? "translationQueuedKorean" : "translationQueued") :
          t(state.locale === "ko" ? "translatingAnswerKorean" : "translatingAnswer")),
        el("p", { class: "muted small" }, t(state.locale === "ko" ? "translationOriginalVisibleKorean" : "translationOriginalVisible")),
        el("p", { class: "muted small" },
          task.job?.queue_position ? `${ui("대기 순서 {position}", { position: number(task.job.queue_position) })} · ` : "",
          ui("{seconds}초 경과", { seconds: number(elapsed) }))), cancel);
  }
  const reason = result.translation_error ? answerMessage(result.translation_error) :
    task?.job?.error ? `${t("translationUnavailable")} ${ui(task.job.error)}` : "";
  const retry = button(t(state.locale === "ko" ? "retryTranslationKorean" : "retryTranslation"), requestAnswerTranslation, "secondary compact");
  if (options.suppressNotice) return el("div", { class: "translation-status actions" }, retry);
  return el("div", { class: "translation-status" },
    notice(task?.status === "cancelled" ? t("translationCancelled") :
      reason ? reason :
      task?.error ? `${t("translationUnavailable")} ${errorText(task.error)}` :
        t(state.locale === "ko" ? "translationOriginalVisibleKorean" : "translationOriginalVisible"), "warning"), retry);
}
function canFollowUp(result) {
  const evidenceIds = new Set((result.evidence || []).map(item => item.evidence_id));
  const claims = result.claims || [];
  const blocked = ["model_unclear", "version_applicability_unclear", "source_conflict", "visual_review_pending", "question_scope_unclear", "applicability_unconfirmed", "explanation_unverified"];
  if (result.conflicts?.length || blocked.some(code => result.limitation_codes?.includes(code))) return false;
  const limitations = new Set(result.limitation_codes || []);
  const deterministicClarification = result.status === "clarification_required" &&
    result.inquiry_mode === "troubleshooting" && result.consultation_state === "active" &&
    result.limitation_codes?.includes("symptom_confirmation_required") &&
    !!result.evidence?.length && !!result.questions?.length && !claims.length;
  if (deterministicClarification) return true;
  if (!claims.length || claims.some(claim => !claim.citation_ids?.length || claim.citation_ids.some(id => !evidenceIds.has(id)))) return false;
  if (result.status === "answered") return true;
  if (result.status === "partial_answer" && claims.length && claims.every(directClaim) && !result.topics?.length && limitations.has("incomplete_evidence")) return true;
  return ["clarification_required", "service_required"].includes(result.status) &&
    result.limitation_codes?.includes("symptom_confirmation_required") && !!result.questions?.length && !result.topics?.length;
}
const followupOptionLabels = {
  "프린터 용지가 나오지 않아요": "Printer paper does not come out",
  "용지는 나오지만 인쇄가 안 돼요": "Paper feeds but nothing is printed",
  "프린터에 오류 문구가 표시돼요": "The printer displays an error message",
  "전원이 켜지지 않아요": "The device does not power on",
  "전원은 켜지지만 화면에 이상이 있어요": "Power is on but the screen has a problem",
  "오류 문구가 표시돼요": "An error message is displayed",
  "특정 기능만 작동하지 않아요": "Only a particular function does not work",
  "프린터 용지가 나오지 않음": "Printer paper does not come out",
  "빈 출력": "Blank printout",
  "오류 문구가 표시됨": "An error message is displayed",
  "전원이 켜지지 않음": "The device does not power on",
  "화면이 반응하지 않음": "The screen does not respond",
  "소리가 나지 않음": "No sound",
  "측정값이 표시되지 않음": "Measurement values are not displayed",
};
function followupOptionLabel(option) {
  if (option === "해결됨" || option === "Resolved") return ui("해결됨");
  if (option === "해결 안 됨" || option === "Not resolved") return ui("해결 안 됨");
  if (option === "이미 해봤음" || option === "Already tried") return ui("이미 해봤음");
  if (state.locale === "en" && followupOptionLabels[option]) return followupOptionLabels[option];
  if (state.locale === "en" && /[가-힣]/.test(option)) return ui("Other symptom");
  return option;
}
function directClaim(claim) {
  // Old records and unverified excerpts default to original-view only.
  return claim?.presentation === "direct";
}
function directClaims(result) { return (result?.claims || []).filter(directClaim); }
function citedEvidence(result) {
  const evidence = result?.evidence || [];
  const cited = new Set((result?.claims || []).flatMap(claim => claim.citation_ids || []));
  for (const conflict of result?.conflicts || []) for (const variant of conflict.variants || []) for (const id of variant.citation_ids || []) cited.add(id);
  const selected = evidence.filter(item => cited.has(item.evidence_id));
  return selected.length ? selected : evidence;
}
function sourceButton(job, result) {
  if (!result) return null;
  const count = new Set(citedEvidence(result).map(item => item.document_id)).size;
  return button(`${ui("원문 보기")} (${number(count)})`, () => openSourceDialog(job, result), "secondary compact", "book");
}
async function openSourceDialog(job, result) {
  const body = el("div", { class: "source-dialog" }, el("p", { class: "muted" }, ui("답변에 사용된 문서와 원문 발췌를 불러오는 중입니다.")));
  showDialog(ui("원문 보기"), body);
  const session = state.sessionVersion; const jobId = job?.id;
  let fresh = result;
  if (jobId) {
    try {
      const checked = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
      if (session !== state.sessionVersion || !dialog.open || !body.isConnected) return;
      if (state.currentJob?.id !== jobId || checked.status !== "completed" || !checked.result) {
        throw new Error(ui("현재 답변의 원문 접근 권한을 확인할 수 없습니다. 답변을 다시 열어 주세요."));
      }
      fresh = checked.result;
    } catch (error) {
      if (session !== state.sessionVersion || !dialog.open || !body.isConnected) return;
      body.replaceChildren(notice(`${ui("직접 설명을 확인하지 못했습니다.")} ${errorText(error)}`, "warning"), el("div", { class: "actions" },
        button(ui("다시 시도"), () => openSourceDialog(job, result), "secondary compact"),
        button(ui("닫기"), () => dialog.close(), "ghost compact")));
      return;
    }
  }
  if (!dialog.open || !body.isConnected || session !== state.sessionVersion) return;
  const sources = citedEvidence(fresh);
  const original = el("details", { class: "original-answer", translate: "no" }, el("summary", {}, ui("원문 답변 보기")), ...answerContent(fresh, false, { originalView: true }));
  const cited = (fresh.claims || []).some(claim => claim.citation_ids?.length) || (fresh.conflicts || []).some(conflict => conflict.variants?.some(variant => variant.citation_ids?.length));
  body.replaceChildren(...[
    fresh.message && notice(answerMessage(fresh.message, false)),
    !cited && el("p", { class: "small muted" }, ui("검색된 관련 자료이며, 질문의 답을 확인한 근거는 아닙니다.")),
    original,
    sources.length ? el("div", { class: "source-dialog-list" }, el("h3", {}, cited ? ui("확인한 문서 {count}개", { count: number(new Set(sources.map(item => item.document_id)).size) }) : ui("관련 원문 {count}개", { count: number(new Set(sources.map(item => item.document_id)).size) })), ...sources.map((source, index) => renderEvidence(source, index, false))) :
      notice(ui("확인된 근거 문서가 없습니다."), "warning")].filter(Boolean));
}
function answerContent(result, translated, options = {}) {
  const evidence = result.evidence || [];
  const originalView = options.originalView === true;
  const claims = originalView ? (result.claims || []) : directClaims(result);
  const claimNode = claim => el("p", { class: "claim" },
    answerText(result, `claim:${claim.id}`, claim.text, translated), originalView && citationNodes(claim.citation_ids, evidence));
  const topicStatus = {
    answered: "확인됨", partial_answer: "일부 확인", evidence_missing: "근거 부족",
    clarification_required: "추가 확인 필요", service_required: "서비스 확인 필요",
    visual_review_required: "원문 배치 확인 필요", conflict: "근거 충돌", search_results: "원문 검색",
  };
  const topicClaims = new Set((result.topics || []).flatMap(topic => topic.claim_ids));
  const conflictNodes = (result.conflicts || []).map((conflict, index) => {
    const variants = (conflict.variants || []).map((variant, variantIndex) =>
      el("div", { class: "conflict-variant" }, el("p", {}, answerText(result, `conflict:${index}:variant:${variantIndex}`, variant.text, translated)), originalView && citationNodes(variant.citation_ids, evidence)));
    return el("div", { class: "conflict" }, el("h3", {}, answerText(result, `conflict:${index}:topic`, conflict.topic, translated)), variants);
  });
  return [
    ...(result.topics || []).map(topic => el("section", { class: "answer-topic" },
      el("h3", {}, ui(topic.label)),
      el("p", { class: "small muted" }, claims.some(claim => topic.claim_ids.includes(claim.id)) ? ui(topicStatus[topic.status] || "추가 확인 필요") : ui("원문 확인 필요")),
      ...claims.filter(claim => topic.claim_ids.includes(claim.id)).map(claimNode))),
    ...claims.filter(claim => !topicClaims.has(claim.id)).map(claimNode),
    ...(originalView ? conflictNodes : []),
    (result.questions || []).length ? el("div", { class: "notice" }, el("div", {},
      el("h3", {}, ui("추가로 확인해 주세요")), el("ul", {}, result.questions.map((text, index) =>
        el("li", {}, answerText(result, `question:${index}`, text, translated)))))) : null,
  ];
}
function conciseResultNotice(result) {
  const codes = new Set(result.limitation_codes || []);
  const messages = [];
  if (["resolved", "exhausted"].includes(result.status) || ["resolved", "exhausted"].includes(result.consultation_state)) {
    if (result.message) messages.push(answerMessage(result.message));
  }
  if (codes.has("no_documented_next_step")) messages.push(ui("이미 시도한 음량 조작은 반복하지 않겠습니다. 이번에 검색한 근거에서는 다음 점검 방법을 확인하지 못했습니다."));
  if (codes.has("version_applicability_unclear") || codes.has("model_unclear")) messages.push(ui("제품 모델·버전 적용 범위를 먼저 확인하세요."));
  if (codes.has("source_conflict") || result.status === "conflict") messages.push(ui("문서 간 내용이 달라 해당 개정과 적용 조건을 확인하세요."));
  if (codes.has("service_review_required") || codes.has("service_authorization_required") || result.status === "service_required") messages.push(ui("서비스 담당자의 확인이 필요한 항목입니다."));
  if (codes.has("visual_review_pending") || result.status === "visual_review_required") messages.push(ui("표·그림의 원문 배치를 확인해야 하므로 답변을 보류했습니다."));
  if (codes.has("incomplete_evidence") && directClaims(result).length) messages.push(ui("확인된 내용만 안내합니다."));
  if (codes.has("question_scope_unclear")) messages.push(ui("질문을 한 가지씩 나누어 주세요."));
  const sourceOnlyIssue = codes.has("explanation_unverified") || (!directClaims(result).length && (result.claims || []).length && !(result.questions || []).length && !messages.length);
  if (sourceOnlyIssue) {
    messages.push(result.translation_error
      ? ui("관련 근거는 찾았지만 한국어 설명을 확인하지 못했습니다. 원문 보기로 확인하거나 다시 시도해 주세요.")
      : ui("관련 근거는 찾았지만 설명의 정확성을 확인하지 못했습니다. 원문 보기를 눌러 확인해 주세요."));
  } else if (!directClaims(result).length && !(result.claims || []).length && !(result.questions || []).length && !messages.length) {
    messages.push(ui("선택한 매뉴얼에서 질문에 대한 답을 확인하지 못했습니다."));
  }
  if (result.translation_error && !sourceOnlyIssue) messages.push(answerMessage(result.translation_error));
  return messages.join(" ");
}
function renderAnswer(question, result, job = null) {
  const evidence = result.evidence || [];
  const translated = hasAnswerTranslation(result);
  const foreignAnswer = answerNeedsTranslation(result);
  const output = el("section", { class: "answer-section", "aria-label": ui("질문 결과") },
    el("p", { class: "question-label" }, ui("내 질문")), el("h2", { class: "asked-question" }, question),
    el("div", { class: "answer-heading" }, el("h3", {}, result.mode === "ai" || directClaims(result).length ? ui("매뉴얼에 근거한 답변") : ui("관련 매뉴얼 검색 결과")),
      el("div", { class: "actions" }, badge(result.mode === "ai" ? ui("로컬 AI") : ui("원문 검색"), "accent"),
        Number.isFinite(result.elapsed_seconds) && result.elapsed_seconds > 0 ? el("span", { class: "small muted" }, ui("{seconds}초", { seconds: number(Math.round(result.elapsed_seconds * 10) / 10) })) : null)));
  const concise = conciseResultNotice(result);
  if (concise) {
    const terminal = ["resolved", "exhausted"].includes(result.status) || ["resolved", "exhausted"].includes(result.consultation_state);
    output.append(notice(concise, terminal ? (result.status === "resolved" || result.consultation_state === "resolved" ? "success" : "") : "warning"));
  }
  const needsManualChoice = result.status === "clarification_required" &&
    result.limitation_codes?.includes("version_applicability_unclear") &&
    state.selectedProduct && result.evidence?.some(source => source.product_id === state.selectedProduct);
  if (needsManualChoice) {
    const choose = button(ui("사용할 매뉴얼 선택"), () => {
      const filters = document.querySelector("#source-filters");
      if (!filters) return;
      filters.open = true;
      const select = document.querySelector("#source-document");
      select?.scrollIntoView?.({ block: "center", behavior: "smooth" });
      select?.focus();
    }, "secondary compact", "book");
    output.append(el("div", { class: "clarification-action" }, choose,
      el("p", { class: "form-help" }, ui("매뉴얼을 선택한 뒤 질문 보내기를 누르세요."))));
  }
  const translationStatus = translationNotice(result);
  if (translationStatus) output.append(concise ? translationNotice(result, { suppressNotice: true }) : translationStatus);
  const direct = directClaims(result);
  if (foreignAnswer && !translated) {
    if (!concise && !translationStatus) output.append(notice(ui("직접 설명을 확인하지 못했습니다."), "warning"));
  } else output.append(...answerContent(result, translated).filter(Boolean));
  if (!direct.length && result.claims?.length && !concise && !translationStatus) output.append(notice(ui("직접 설명을 확인하지 못했습니다. 원문에서 확인할 수 있습니다."), "warning"));
  if (result.status === "conflict" && !result.message) output.append(notice(ui("문서의 설명이 서로 다릅니다. 제품의 적용 사양과 문서 개정을 담당자에게 확인하세요."), "warning"));
  if (evidence.length) output.append(el("div", { class: "answer-source-action" }, sourceButton(job, result)));
  const inquiryMode = result.inquiry_mode || job?.inquiry_mode || state.inquiryMode;
  if (inquiryMode === "troubleshooting" && job?.status === "completed" &&
      canFollowUp(result)) {
    const followupOptions = result.followup_options?.length
      ? result.followup_options
      : result.claims?.length ? [ui("해결됨"), ui("해결 안 됨"), ui("이미 해봤음")] : [];
    const submitFollowup = (text) => {
      state.followupParentJob = job;
      state.inquiryMode = "troubleshooting";
      state.question = text;
      renderAsk(document.querySelector("#main"));
      document.querySelector("#question")?.focus();
      document.querySelector("#question")?.form?.requestSubmit();
    };
    const followup = el("div", { class: "troubleshooting-followup", role: "group", "aria-label": ui("고장 상담 후속 확인") },
      el("p", { class: "small muted" }, ui(!result.claims?.length && result.status === "clarification_required"
        ? "해당하는 증상을 선택하거나 확인 질문에 직접 답해 주세요."
        : "안내를 적용한 뒤 결과를 선택하세요. 실제 장비 조작이나 해결 여부를 자동으로 판단하지 않습니다.")),
      el("div", { class: "answer-actions" }, followupOptions.map(option => {
        const label = followupOptionLabel(option);
        return button(label, () => submitFollowup(option), "secondary compact");
      })));
    output.append(followup);
  }
  if (has("read_service") && question) output.append(el("details", { class: "related-service-cases" },
    el("summary", {}, ui("유사 서비스 사례")),
    el("p", { class: "small muted" }, ui("매뉴얼 답변과 별도로 과거 서비스 처리 이력을 참고 사례로 확인합니다.")),
    button(ui("유사 사례 찾기"), () => { state.serviceCaseQuery = question; state.serviceSelectedModel = state.selectedProduct || ""; state.serviceCaseMarket = ""; state.serviceCases = []; state.serviceCaseAnswer = ""; state.view = "service"; renderShell(); }, "secondary compact", "wrench")
  ));
  if (!(result.claims || []).length && !(result.conflicts || []).length && !evidence.length && !result.message) output.append(notice(ui("답변을 뒷받침할 자료가 없습니다. 제품과 질문 내용을 확인하거나 필요한 매뉴얼을 추가해 주세요."), "warning"));
  if (job?.status === "completed" && canFollowUp(result)) {
    output.append(el("div", { class: "answer-actions" },
      button(result.status === "answered" ? ui("이 답변에 이어 질문") : ui("확인 질문에 답하기"), () => {
        state.followupParentJob = job;
        if (job.filters) state.sourceFilters = { ...job.filters };
        state.question = "";
        renderAsk(document.querySelector("#main"));
        document.querySelector("#question")?.focus();
      }, "secondary compact", "chat"),
      button(ui("새 질문"), () => { resetQuestionScope(); state.question = ""; state.currentJob = null; renderAsk(document.querySelector("#main")); }, "ghost compact", "plus")));
  }
  return output;
}
function renderEvidence(source, index, expanded = false) {
  const displayText = source.text.replace(/^(?:SOURCE:|PDF_PAGE:|ORIGINAL_MEMBER:)[^\n]*(?:\r?\n|$)/gm, "").replace(/\n(?:[ \t]*\n){2,}/g, "\n\n").trim();
  return el("article", { class: "evidence-item", translate: "no" },
    el("div", { class: "evidence-meta" }, el("span", { class: "evidence-number" }, index + 1), el("span", { class: "small muted" }, source.product_id)),
    link(source.title, docUrl(source.document_id, source.physical_page), "evidence-title"),
    el("p", {}, `${kindLabel(source.kind)} · ${source.revision || ui("개정 미지정")} · ${source.language || ""}`),
    source.original_name && el("p", { class: "source-filename" }, ui("근거 파일: {name}", { name: source.original_name })),
    el("p", {}, ui("대상 국가 / 시장: {country}", { country: source.country || ui("미확인") })),
    documentStatusDetails({ ...source, needs_review: source.layout_reviewed === false }),
    el("p", {}, ui("{format} 물리 {page}페이지", { format: source.is_derived_pdf ? ui("변환 PDF") : "PDF", page: number(source.physical_page) })),
    el("details", { open: expanded || null }, el("summary", {}, ui("원문 발췌 보기")), el("div", { class: "evidence-text" }, displayText)));
}
function renderReferencePane(pane, result) {
  if (!pane) return;
  if (result?.evidence?.length) {
    pane.replaceChildren(el("h2", {}, ui("답변 근거")), el("p", {}, ui("원문은 버튼을 눌렀을 때만 표시됩니다.")), sourceButton(state.currentJob, result));
    return;
  }
  pane.replaceChildren(el("h2", {}, ui("답변 근거")), el("p", {}, ui("질문을 보내면 답변에 사용된 원문을 확인할 수 있습니다.")));
}

function renderDocuments(main) {
  const actions = has("manage_documents") ? [
    button(ui("보류 첨부"), showHeldAttachments, "secondary", "file"),
    button(ui("기존 자료 가져오기"), importCorpus, "secondary", "download"),
    button(ui("매뉴얼 추가"), showUpload, "", "plus"),
  ] : [];
  const product = productSelect("document-filter", true, state.docFilter);
  const status = el("select", { id: "document-state", "aria-label": ui("문서 상태 필터") }, el("option", { value: "" }, ui("모든 상태")),
    Object.entries(stateKeys).map(([key, label]) => el("option", { value: key, selected: key === state.docState }, t(label))));
  const query = input("document-query", "search", { class: "search-field", placeholder: ui("제목·파일명·승인일 검색"), "aria-label": ui("제목·파일명·승인일 검색"), value: state.docQuery });
  const filterDefinitions = [
    ["document_language", "language", ui("근거 문서 언어"), ui("모든 문서 언어"), sourceLanguageLabel],
    ["kind", "kind", ui("문서 종류"), ui("모든 문서 종류"), kindLabel],
    ["country", "country", ui("대상 국가 / 시장"), ui("모든 국가 / 시장")],
    ["revision", "revision", ui("개정"), ui("모든 개정")],
  ];
  const makeFilter = ([filter, key, label, allLabel, labelFor]) => {
    const control = metadataSelect(`library-${filter}`, state.documents, key, state.libraryFilters[filter], allLabel, labelFor);
    control.addEventListener("change", () => { state.libraryFilters[filter] = control.value; state.docPage = 1; clearLibrarySelection(); renderDocumentList(); });
    return field(label, control);
  };
  const primaryFilters = filterDefinitions.slice(0, 2).map(makeFilter);
  const filters = filterDefinitions.slice(2).map(makeFilter);
  const availability = el("select", { id: "document-availability", "aria-label": ui("활성 상태 필터") },
    el("option", { value: "" }, ui("모든 활성 상태")), el("option", { value: "enabled", selected: state.libraryAvailability === "enabled" }, ui("활성")),
    el("option", { value: "disabled", selected: state.libraryAvailability === "disabled" }, ui("비활성")));
  availability.addEventListener("change", () => { state.libraryAvailability = availability.value; state.docPage = 1; clearLibrarySelection(); renderDocumentList(); });
  const trash = el("select", { id: "document-trash", "aria-label": ui("휴지통 상태 필터") },
    el("option", { value: "", selected: !state.libraryTrash }, ui("휴지통 제외")),
    el("option", { value: "trashed", selected: state.libraryTrash === "trashed" }, ui("휴지통 문서")),
    el("option", { value: "all", selected: state.libraryTrash === "all" }, ui("휴지통 포함")));
  trash.addEventListener("change", () => { state.libraryTrash = trash.value === "all" ? "all" : trash.value; state.docPage = 1; clearLibrarySelection(); renderDocumentList(); });
  status.addEventListener("change", () => { state.docState = status.value; state.docPage = 1; clearLibrarySelection(); renderDocumentList(); });
  product.addEventListener("change", () => { state.docFilter = product.value; state.docPage = 1; clearLibrarySelection(); renderDocumentList(); });
  query.addEventListener("input", () => { state.docQuery = query.value; state.docPage = 1; clearLibrarySelection(); renderDocumentList(); });
  const primary = el("div", { class: "library-primary-filters" }, query, product, ...primaryFilters);
  const advanced = el("details", { class: "library-advanced" }, el("summary", {}, ui("상세 필터")),
    el("div", { class: "library-filters" }, ...filters, has("manage_documents") && field(ui("문서 상태 필터"), status), has("manage_documents") && field(ui("활성 상태"), availability), has("manage_documents") && field(ui("휴지통"), trash)),
    has("manage_documents") && button(ui("제품 등록"), showProductForm, "secondary compact", "plus"));
  main.replaceChildren(pageHeading(ui("매뉴얼 자료실"), has("manage_documents") ? ui("문서를 등록하고 원문을 검토한 뒤 직원에게 게시합니다.") : ui("열람할 수 있는 제품 매뉴얼과 개정을 확인하세요."), actions),
    primary, advanced, el("div", { class: "filter-summary" }),
    el("div", { class: "library-results-meta" }, el("p", { id: "document-count", class: "small muted" }), el("div", { id: "document-page-size" })),
    el("p", { class: "form-help" }, ui("게시 상태는 회사의 승인·유효 여부와 다릅니다. 미확인 정보와 원문 검토 필요 표시를 확인하세요.")),
    el("div", { id: "document-list", class: "data-list" }));
  renderDocumentList();
}
function renderDocumentList() {
  const list = document.querySelector("#document-list");
  if (!list) return;
  const docs = libraryDocuments(); const page = libraryPage(docs);
  const summary = document.querySelector(".filter-summary");
  if (summary) {
    const active = [];
    const labels = { document_language: ui("근거 문서 언어"), country: ui("대상 국가 / 시장"), kind: ui("문서 종류"), revision: ui("개정") };
    if (state.docFilter) active.push(`${ui("제품")}: ${libraryProductLabel(state.docFilter)}`);
    if (state.docState) active.push(`${ui("상태")}: ${stateLabel(state.docState)}`);
    if (state.docQuery.trim()) active.push(`${ui("검색")}: ${state.docQuery.trim()}`);
    for (const [key, value] of Object.entries(state.libraryFilters)) if (value) {
      const display = key === "document_language" ? sourceLanguageLabel(value) : key === "kind" ? kindLabel(value) : value;
      active.push(`${labels[key] || key}: ${display}`);
    }
    summary.replaceChildren(...active.map(item => badge(item)), ...(active.length ? [button(ui("필터 초기화"), () => {
      state.docFilter = ""; state.docState = ""; state.docQuery = ""; state.libraryFilters = {}; state.libraryAvailability = ""; state.docPage = 1; clearLibrarySelection(); renderDocuments(document.querySelector("#main"));
    }, "ghost compact")] : []));
  }
  const focusList = () => { list.focus({ preventScroll: true }); list.scrollIntoView?.({ block: "start", behavior: "smooth" }); };
  const pageNumbers = Array.from({ length: Math.min(5, page.pages) }, (_, index) => {
    const start = Math.min(Math.max(1, page.page - 2), Math.max(1, page.pages - 4)); return start + index;
  });
  const pages = el("nav", { class: "library-pagination", "aria-label": ui("자료실 페이지" ) },
    button(ui("이전"), () => { state.docPage--; renderDocumentList(); focusList(); }, "secondary compact"),
    ...pageNumbers.map(numberValue => {
      const control = button(String(numberValue), () => { state.docPage = numberValue; renderDocumentList(); focusList(); }, numberValue === page.page ? "compact" : "secondary compact");
      control.setAttribute("aria-current", numberValue === page.page ? "page" : "false"); return control;
    }),
    el("span", { class: "library-page-label" }, ui("{page} / {pages}페이지", { page: number(page.page), pages: number(page.pages) })),
    button(ui("다음"), () => { state.docPage++; renderDocumentList(); focusList(); }, "secondary compact"));
  pages.querySelectorAll("button")[0].disabled = page.page <= 1;
  pages.querySelectorAll("button")[pages.querySelectorAll("button").length - 1].disabled = page.page >= page.pages;
  const meta = document.querySelector("#document-count");
  if (meta) meta.textContent = docs.length ? ui("{start}-{end} / {count}개 문서", { start: number(page.start), end: number(page.end), count: number(page.total) }) : ui("0개 문서");
  const pageHost = document.querySelector("#document-page-size");
  if (pageHost && !pageHost.firstChild) {
    const size = el("select", { id: "document-page-size-select", "aria-label": ui("페이지당 문서 수") }, el("option", { value: "10", selected: state.docPageSize === 10 }, ui("10개씩")), el("option", { value: "20", selected: state.docPageSize === 20 }, ui("20개씩")));
    size.addEventListener("change", () => { state.docPageSize = Number(size.value) === 20 ? 20 : 10; state.docPage = 1; renderDocumentList(); });
    pageHost.replaceChildren(el("label", { class: "library-page-size", for: size.id }, ui("페이지당"), size));
  }
  list.tabIndex = -1;
  list.parentElement?.querySelector(".library-pagination")?.remove();
  list.parentElement?.append(pages);
  if (!docs.length) {
    list.replaceChildren(...[bulkResultNotice(), empty(ui("표시할 매뉴얼이 없습니다"), state.documents.length ? ui("필터나 검색어를 바꾸어 보세요.") : ui("관리자가 매뉴얼을 등록하고 게시하면 이곳에 표시됩니다."))].filter(Boolean));
    for (const control of pages.querySelectorAll("button")) control.disabled = true;
    return;
  }
  const selectedPage = page.items.filter(document => state.librarySelection.has(document.id)).length;
  const allPageSelected = selectedPage === page.items.length && page.items.length > 0;
  const toolbar = has("manage_documents") ? el("div", { class: "library-bulk-toolbar" },
    el("label", { class: "check-label" }, input("select-library-page", "checkbox", { checked: allPageSelected, disabled: state.bulkBusy, "aria-label": ui("현재 페이지 선택"), onchange: event => toggleLibraryPage(page.items, event.currentTarget.checked) }), el("span", {}, ui("현재 페이지 선택"))),
    button(ui("검색 결과 전체 선택"), () => { if (docs.length > 500) { toast(ui("한 번에 최대 500개까지 선택할 수 있습니다.")); return; } docs.forEach(document => state.librarySelection.add(document.id)); renderDocumentList(); }, "secondary compact"),
    el("span", { class: "library-selection-count" }, ui("{count}개 선택", { count: number(state.librarySelection.size) })),
    button(ui("활성화"), () => bulkAvailability(true), "compact", "shield"),
    button(ui("비활성화"), () => bulkAvailability(false), "secondary compact"),
    button(ui("휴지통으로 이동"), () => bulkLifecycle("trash"), "secondary compact"),
    state.libraryTrash === "trashed" && button(ui("복원"), () => bulkLifecycle("restore"), "compact"),
    button(ui("선택 해제"), () => { state.librarySelection.clear(); renderDocumentList(); }, "ghost compact")) : null;
  if (toolbar) {
    const pageCheckbox = toolbar.querySelector("input");
    if (pageCheckbox) pageCheckbox.indeterminate = selectedPage > 0 && !allPageSelected;
    toolbar.querySelectorAll("button, input").forEach(control => { control.disabled = state.bulkBusy; });
    if (!state.librarySelection.size) [...toolbar.querySelectorAll("button")].slice(1).forEach(control => { control.disabled = true; });
  }
  const resultNotice = bulkResultNotice();
  list.replaceChildren(...[toolbar, resultNotice, el("div", { class: `document-row row-heading${has("manage_documents") ? " managed-row" : ""}`, "aria-hidden": "true" }, has("manage_documents") && el("span", {}, ui("선택")), el("span", {}, ui("매뉴얼 / 개정")), el("span", {}, ui("상태")), el("span", {}, ui("열람 / 관리"))),
    ...page.items.map(document => {
      const excluded = blockedValidity(document.validity);
      const validity = (document.validity || "").toLowerCase() === "metadata_conflict" ? ui("메타데이터 불일치 · 검토 필요") :
        excluded ? ui("유효 자료에서 제외") : (document.validity || "").toLowerCase() === "valid" ? ui("유효 확인") :
        (document.approval_status || "").toLowerCase() !== "approved" ? ui("승인·유효 여부 미확인") : ui("유효 여부 미확인");
      const shortStatus = [document.trashed && ui("휴지통"), pendingApproval(document.approval_status) ? ui("결재 미진행 · 답변 제외") : blockedApproval(document.approval_status) && ui("승인 대상 제외"),
        validity, document.needs_review && ui("원문 검토 필요")].filter(Boolean);
      return el("article", { class: `document-row${has("manage_documents") ? " managed-row" : ""}`, translate: "no" },
        has("manage_documents") && el("div", { class: "document-select" }, input(`select-document-${document.id}`, "checkbox", { checked: state.librarySelection.has(document.id), disabled: state.bulkBusy, "aria-label": ui("문서 선택: {title}", { title: document.title }), onchange: event => { event.stopPropagation(); toggleLibraryPage([document], event.currentTarget.checked); } })),
        el("div", {}, el("h3", {}, document.title),
          el("div", { class: "document-meta" }, el("span", {}, document.product_id), el("span", {}, kindLabel(document.kind)), el("span", {}, document.revision), el("span", {}, sourceLanguageLabel(document.language)), el("span", {}, document.country || ui("국가 미확인"))),
          el("p", { class: "small muted" }, shortStatus.join(" · "))),
        el("div", { class: "state-cell" }, badge(stateLabel(document.state), document.state === "published" ? "success" : document.state === "processing_failed" ? "danger" : "warning"), badge(document.enabled === false ? ui("비활성") : ui("활성"), document.enabled === false ? "warning" : "success")),
        el("div", { class: "actions document-actions document-page-actions" }, link(ui("PDF 열기"), docUrl(document.id), "btn secondary compact"), button(ui("문서 정보 / 첨부"), () => showDocumentInfo(document), "ghost compact")));
    })].filter(Boolean));
}
function bulkResultNotice() {
  const batchResult = state.libraryBatchResult;
  if (!batchResult || !has("manage_documents")) return null;
  return el("div", { class: "library-bulk-result", role: "status" },
    ui("처리 결과: 변경 {updated}개 · 변경 없음 {unchanged}개 · 제외 {excluded}개", { updated: number(batchResult.updated || 0), unchanged: number(batchResult.unchanged || 0), excluded: number(batchResult.excluded || 0) }),
    ...(batchResult.items || []).filter(item => item.status === "excluded").map(item => el("p", { class: "small muted" }, `${state.documents.find(doc => doc.id === item.document_id)?.title || item.document_id}: ${ui(item.reason || "처리 제외")}`)));
}
function attachmentRole(role) {
  return role === "manual" ? ui("매뉴얼 원본") : role === "approval" ? ui("승인 관련 첨부 · 답변 제외") : ui("관련 첨부 · 답변 제외");
}
function attachmentItem(file, showRecord = false) {
  return el("div", { class: "attachment-item", translate: "no" },
    link(file.name, `/api/library-assets/${encodeURIComponent(file.id)}`, "attachment-link"),
    el("span", { class: "small muted" }, attachmentRole(file.role)),
    showRecord && el("span", { class: "small muted" }, ui("원본 목록 번호: {record}", { record: file.record_id || ui("미확인") })),
    (file.reason || showRecord) && el("span", { class: "small muted" }, file.reason || ui("보류 사유 미기록")),
    showRecord && has("manage_documents") && !file.document_id && button(ui("검토 후 추가"), () => reviewHeldAttachment(file), "secondary compact"));
}
function reviewHeldAttachment(file) {
  const product = productSelect("held-product", false, state.selectedProduct);
  const kind = el("select", { id: "held-kind" }, Object.entries(kindKeys).map(([key, label]) => el("option", { value: key }, t(label))));
  const title = input("held-title", "text", { required: true, maxlength: 200, value: file.name.replace(/\.[^.]+$/, "") });
  const revision = input("held-revision", "text", { required: true, maxlength: 40 });
  const language = input("held-language", "text", { required: true, maxlength: 20, value: "KOR" });
  const country = input("held-country", "text", { required: true, maxlength: 32, placeholder: ui("예: KOR, EUR") });
  const mode = el("select", { id: "held-mode" }, el("option", { value: "register" }, ui("새 매뉴얼로 등록")), el("option", { value: "link" }, ui("기존 매뉴얼 첨부로 연결")));
  const existing = el("select", { id: "held-existing", hidden: true, "aria-label": ui("연결할 기존 매뉴얼") }, el("option", { value: "" }, ui("기존 매뉴얼을 선택하세요")), ...state.documents.filter(document => !document.trashed).map(document => el("option", { value: document.id }, `${document.product_id} · ${document.title}`)));
  const existingField = field(ui("연결할 기존 매뉴얼"), existing); existingField.hidden = true;
  const note = el("textarea", { id: "held-note", rows: "3", maxlength: 500, required: true, placeholder: ui("제품·언어·개정·보류 사유를 확인한 기록") });
  const submit = el("button", { type: "submit", class: "btn" }, ui("자료실 추가 검토 제출"));
  const updateMode = () => { const linking = mode.value === "link"; existingField.hidden = !linking; existing.hidden = !linking; title.required = !linking; kind.required = !linking; revision.required = !linking; language.required = !linking; country.required = !linking; product.required = !linking; };
  mode.addEventListener("change", updateMode);
  const form = el("form", { class: "form-stack" }, notice(ui("이 작업은 자료실 등록 검토이며 회사의 최종 승인 상태를 변경하지 않습니다."), "warning"), field(ui("등록 방식"), mode), field(ui("제품"), product), existingField, field(ui("제목"), title), field(ui("문서 종류"), kind), field(ui("개정"), revision), field(ui("언어"), language), field(ui("대상 국가 / 시장"), country), field(ui("검토 기록"), note), el("div", { class: "dialog-footer" }, submit, button(ui("보류 유지"), () => dialog.close(), "ghost compact", "x")));
  form.addEventListener("submit", async event => { event.preventDefault(); if (!form.reportValidity() || (mode.value === "link" && !existing.value)) return; await busy(submit, async () => { try { const linked = mode.value === "link" ? state.documents.find(document => document.id === existing.value) : null; await api(`/api/library-assets/${encodeURIComponent(file.id)}/review`, { method: "POST", body: { action: mode.value, reason: note.value.trim(), product_id: linked?.product_id || product.value, document_id: linked?.id, title: title.value.trim(), kind: kind.value, revision: revision.value.trim(), language: language.value.trim(), country: country.value.trim() } }); dialog.close(); await refreshData(); renderShell(); toast(ui("보류 자료 검토를 제출했습니다.")); showHeldAttachments(); } catch (error) { dialogError(form, error); } }, ui("자료를 등록 검토 중…")); });
  updateMode(); showDialog(ui("보류 첨부 검토"), form);
}
function unlinkedAttachments(files) {
  const held = file => ["quarantined", "excluded"].includes(file.status);
  return files.filter(file => !file.document_id).sort((a, b) => Number(held(b)) - Number(held(a)));
}
async function showHeldAttachments() {
  if (!has("manage_documents")) return;
  const list = el("div", { class: "attachment-list", role: "status" }, ui("첨부파일을 불러오는 중입니다."));
  const body = el("div", {}, notice(ui("매뉴얼에 연결되지 않은 보류 자료입니다. 원본을 확인하고 검토 후 추가할 수 있으며, 게시 전에는 검색·답변에 사용하지 않습니다."), "warning"), list);
  showDialog(ui("보류 첨부"), body);
  const session = state.sessionVersion;
  try {
    const files = unlinkedAttachments(await api("/api/library/catalog"));
    if (!dialog.open || !body.isConnected || session !== state.sessionVersion) return;
    list.replaceChildren(...(files.length ? files.map(file => attachmentItem(file, true)) :
      [el("p", { class: "muted" }, ui("매뉴얼에 연결되지 않은 보류 첨부가 없습니다."))]));
  } catch (error) {
    if (dialog.open && body.isConnected && session === state.sessionVersion) list.replaceChildren(notice(errorText(error), "error", "alert"));
  }
}
async function showDocumentInfo(manual) {
  const body = el("div", {},
    el("p", {}, `${manual.product_id} · ${kindLabel(manual.kind)} · ${manual.language} · ${manual.revision}`),
    el("p", {}, ui("대상 국가 / 시장: {country}", { country: manual.country || ui("미확인") })),
    manual.original_name && el("p", { class: "source-filename small muted" }, ui("근거 파일: {name}", { name: manual.original_name })),
    el("p", { class: "small muted" }, ui("{count}페이지", { count: number(manual.page_count) })),
    documentStatusDetails(manual),
    manual.ingestion_note && el("p", { class: "small muted" }, manual.ingestion_note),
    manual.source_record && el("p", { class: "small muted" }, ui("원본 목록 번호: {record}", { record: manual.source_record })),
    el("div", { class: "actions" }, link(ui("PDF 열기"), docUrl(manual.id), "btn secondary"),
      link(ui("원본 다운로드"), `/api/documents/${encodeURIComponent(manual.id)}/original`, "btn secondary"),
      has("manage_documents") && button(ui("검토 / 관리"), () => showReview(manual), "secondary")),
    el("h3", { class: "attachment-heading" }, ui("보존된 원본 및 관련 첨부")),
    el("p", { class: "form-help" }, ui("PDF와 Word가 같은 매뉴얼이면 검색 근거는 하나로 묶습니다. 승인 첨부는 매뉴얼 답변 근거로 사용하지 않습니다.")));
  const attachments = el("div", { class: "attachment-list", role: "status" }, ui("첨부파일을 불러오는 중입니다."));
  body.append(attachments);
  showDialog(manual.title, body);
  const session = state.sessionVersion;
  try {
    const files = await api(`/api/documents/${encodeURIComponent(manual.id)}/attachments`);
    if (!dialog.open || !body.isConnected || session !== state.sessionVersion) return;
    attachments.replaceChildren(...(files.length ? files.map(file => attachmentItem(file)) :
      [el("p", { class: "muted" }, ui("별도 첨부가 없습니다. 위의 원본 다운로드를 이용하세요."))]));
  } catch (error) {
    if (dialog.open && body.isConnected && session === state.sessionVersion) attachments.replaceChildren(notice(errorText(error), "error", "alert"));
  }
}
async function importCorpus(event) {
  await busy(event.currentTarget, async () => {
    try {
      const result = await api("/api/import", { method: "POST" });
      await refreshData(); renderShell(); toast(ui("{imported}개 문서를 가져왔습니다. 중복 {skipped}개는 건너뛰었습니다. 문서별 검토 후 게시하세요.", { imported: number(result.imported), skipped: number(result.skipped) }));
    } catch (error) { toast(errorText(error)); }
  }, ui("자료 가져오는 중…"));
}
function showProductForm() {
  const id = input("new-product-id", "text", { required: true, maxlength: 60, pattern: "[A-Za-z0-9_\\-]+", placeholder: ui("예: BT-750") });
  const name = input("new-product-name", "text", { required: true, maxlength: 80, placeholder: ui("제품 이름") });
  const submit = el("button", { type: "submit", class: "btn" }, ui("제품 등록"));
  const form = el("form", { class: "form-stack" }, field(ui("제품 모델"), id), field(ui("제품 이름"), name), el("div", { class: "dialog-footer" }, submit));
  form.addEventListener("submit", async event => {
    event.preventDefault(); await busy(submit, async () => {
      try { await api("/api/products", { method: "POST", body: { id: id.value.trim(), name: name.value.trim() } }); await refreshData(); dialog.close(); renderShell(); toast(ui("제품을 등록했습니다.")); }
      catch (error) { dialogError(form, error); }
    });
  });
  showDialog(ui("새 제품 등록"), form);
}
function showUpload() {
  if (!(state.allProducts.length || state.products.length)) { showProductForm(); toast(ui("먼저 매뉴얼의 제품을 등록해 주세요.")); return; }
  const product = productSelect("upload-product");
  const title = input("upload-title", "text", { required: true, maxlength: 200, placeholder: ui("예: BT-350 사용자 매뉴얼") });
  const kind = el("select", { id: "upload-kind", required: true }, Object.entries(kindKeys).map(([key, label]) => el("option", { value: key }, t(label))));
  const revision = input("upload-revision", "text", { required: true, maxlength: 40, placeholder: ui("예: R12") });
  const language = input("upload-language", "text", { required: true, maxlength: 20, value: "KOR" });
  const country = input("upload-country", "text", { required: true, maxlength: 32, placeholder: ui("예: KOR, EUR") });
  const file = input("upload-file", "file", { required: true, accept: ".pdf,.docx" });
  const submit = el("button", { type: "submit", class: "btn" }, ui("등록하고 검토 대기"));
  const form = el("form", {},
    el("div", { class: "form-grid" }, field(ui("제품"), product), field(ui("문서 종류"), kind),
      el("div", { class: "full" }, field(ui("문서 제목"), title)), field(ui("개정"), revision), field(ui("언어"), language), field(ui("대상 국가 / 시장"), country),
      el("div", { class: "full" }, field(ui("매뉴얼 파일"), file, ui("PDF 또는 DOCX. 등록 후 원문을 검토해야 질문에 사용됩니다.")))),
    el("div", { class: "dialog-footer" }, submit));
  form.addEventListener("submit", async event => {
    event.preventDefault(); await busy(submit, async () => {
      try {
        const data = new FormData();
        data.append("file", file.files[0]); data.append("product_id", product.value); data.append("title", title.value.trim());
        data.append("kind", kind.value); data.append("revision", revision.value.trim()); data.append("language", language.value.trim()); data.append("country", country.value.trim());
        await api("/api/documents", { method: "POST", body: data }); await refreshData(); dialog.close(); renderShell(); toast(ui("문서를 등록했습니다. 원문을 검토한 뒤 게시하세요."));
      } catch (error) { dialogError(form, error); }
    }, ui("문서 변환·등록 중…"));
  });
  showDialog(ui("매뉴얼 추가"), form);
}
async function showReview(document) {
  const body = el("div", {}, el("p", { class: "muted" }, ui("문서 페이지를 불러오는 중입니다.")));
  showDialog(document.title, body);
  try {
    const pages = await api(`/api/documents/${encodeURIComponent(document.id)}/pages`);
    if (!dialog.open) return;
    const pageSelect = el("select", { id: "review-page", "aria-label": ui("검토할 물리 페이지") }, pages.map(page => el("option", { value: page.number }, ui("PDF {page}페이지", { page: number(page.number) }))));
    const preview = el("div", { class: "review-preview", translate: "no", tabindex: "0", "aria-label": ui("추출된 원문 텍스트") }, pages[0]?.text || ui("텍스트가 없습니다. PDF 원문을 확인하세요."));
    const pdfLink = link(ui("이 페이지 PDF 열기"), docUrl(document.id, pages[0]?.number), "btn secondary compact");
    pageSelect.addEventListener("change", () => { const page = pages.find(item => item.number === Number(pageSelect.value)); preview.textContent = page?.text || ui("텍스트가 없습니다."); pdfLink.href = docUrl(document.id, pageSelect.value); });
    const reviewNote = el("textarea", { id: "review-note", rows: "3", required: true, maxlength: 2000, placeholder: ui("적용 제품·개정·표와 그림·주의사항을 확인한 내용을 기록하세요.") }, document.review_note || "");
    const reviewed = checkbox("layout-reviewed", ui("표·그림·적용 모델을 포함하여 원문 전체를 확인했습니다."), false, ui("추출 텍스트만으로 판단하지 말고 PDF 원문과 비교하세요."));
    reviewed.querySelector("input").required = true;
    const excluded = !eligibleSource({ ...document, state: "published" });
    const submit = el("button", { type: "submit", class: "btn", disabled: excluded }, document.state === "published" ? ui("검토 기록 갱신") : ui("이 문서 게시"));
    const form = el("form", {},
      el("div", { class: "actions" }, badge(document.product_id, "accent"), badge(kindLabel(document.kind)), badge(document.revision),
        badge(stateLabel(document.state), document.state === "published" ? "success" : "warning")),
      el("p", { class: "small muted" }, ui("원본: {name} · {count}페이지", { name: document.original_name, count: number(document.page_count) })),
      documentStatusDetails(document),
      excluded && notice(ui("결재 미진행·효력상실·History 등 제외 자료는 게시할 수 없습니다. 승인된 유효 원본을 별도로 등록하세요."), "warning"),
      el("div", { class: "review-toolbar" }, pageSelect, pdfLink),
      preview, el("p", { class: "form-help" }, ui("페이지 번호는 PDF 파일의 첫 페이지부터 센 물리 페이지입니다.")),
      el("hr", { class: "review-divider" }), field(ui("검토 기록"), reviewNote), el("div", { class: "form-stack" }, el("div", {}, reviewed)),
      el("div", { class: "dialog-footer" },
        link(ui("원본 다운로드"), `/api/documents/${encodeURIComponent(document.id)}/original`, "btn secondary"),
        document.state === "published" && button(ui("문서 철회"), () => showWithdraw(document), "danger"),
        submit));
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (excluded || !form.reportValidity()) return;
      await busy(submit, async () => {
        try {
          await api(`/api/documents/${encodeURIComponent(document.id)}/publish`, { method: "POST", body: { review_note: reviewNote.value.trim(), layout_reviewed: reviewed.querySelector("input").checked } });
          await refreshData(); dialog.close(); renderShell(); toast(ui("문서를 게시했습니다."));
        } catch (error) { dialogError(form, error); }
      }, ui("게시 중…"));
    });
    body.replaceChildren(form);
  } catch (error) { body.replaceChildren(notice(errorText(error), "error", "alert")); }
}
function showWithdraw(document) {
  const confirm = button(ui("철회하기"), async event => {
    await busy(event.currentTarget, async () => {
      try { await api(`/api/documents/${encodeURIComponent(document.id)}/withdraw`, { method: "POST" }); await refreshData(); dialog.close(); renderShell(); toast(ui("문서를 철회했습니다.")); }
      catch (error) { dialogError(body, error); }
    });
  }, "danger");
  const body = el("div", {}, el("p", {}, ui("‘{title}’ 문서를 철회합니다.", { title: document.title })),
    notice(ui("철회하면 직원 검색과 새 답변에서 제외됩니다. 원본은 보존되며 다시 검토해 게시할 수 있습니다."), "warning"),
    el("div", { class: "dialog-footer" }, button(ui("돌아가기"), () => showReview(document), "secondary"), confirm));
  showDialog(ui("문서 철회"), body);
}

async function renderHistory(main) {
  main.replaceChildren(pageHeading(ui("내 질문 기록"), ui("내가 질문했던 내용과 현재 열람 가능한 근거를 확인합니다.")),
    el("p", { role: "status", class: "muted" }, ui("질문 기록을 불러오는 중입니다.")));
  try {
    const jobs = await api("/api/history");
    if (state.view !== "history" || !main.isConnected) return;
    main.replaceChildren(pageHeading(ui("내 질문 기록"), ui("내가 질문했던 내용과 현재 열람 가능한 근거를 확인합니다.")),
      jobs.length ? el("div", { class: "data-list" }, jobs.map(job => el("button", { type: "button", class: "history-row", onclick: async () => {
        try {
          const session = state.sessionVersion; const userId = state.user?.id;
          const selectedJob = await api(`/api/jobs/${encodeURIComponent(job.id)}`);
          if (session !== state.sessionVersion || userId !== state.user?.id || state.view !== "history") return;
          clearTranslationTask(); state.currentJob = selectedJob;
          state.question = "";
          state.followupParentJob = null;
          state.inquiryMode = selectedJob.result?.inquiry_mode || selectedJob.inquiry_mode || "product";
          const sourceProduct = state.currentJob.result?.evidence?.[0]?.product_id || selectedJob.product_id;
          if (sourceProduct) {
            state.selectedProduct = sourceProduct;
            state.sourceFilters = { ...(selectedJob.filters || {}) };
            if (selectedJob.filters?.document_id) state.manualSelections[sourceProduct] = selectedJob.filters.document_id;
          }
          navigate("ask");
        }
        catch (error) { toast(errorText(error)); }
      } }, icon("chat"), el("div", {}, el("h3", {}, job.question), el("p", {}, ui("{mode} · {count}개 출처", { mode: job.result?.mode === "ai" ? ui("로컬 AI 답변") : t("evidenceSearch"), count: number(job.result?.evidence?.length || 0) }))), icon("chevron")))) :
        empty(ui("아직 질문 기록이 없습니다"), ui("제품에 대해 질문하면 완료된 기록이 이곳에 남습니다."), button(ui("제품 질문하기"), () => navigate("ask"), "secondary", "chat")));
  } catch (error) { if (main.isConnected) main.append(notice(errorText(error), "error", "alert")); }
}
async function renderUsers(main) {
  main.replaceChildren(pageHeading(t("users"), t("usersIntro"), [button(t("addStaff"), () => showUserForm(), "", "plus")]),
    el("p", { role: "status", class: "muted" }, t("loadingAccounts")));
  try {
    const users = await api("/api/users");
    if (state.view !== "users" || !main.isConnected) return;
    main.replaceChildren(pageHeading(t("users"), t("usersManageIntro"), [button(t("addStaff"), () => showUserForm(), "", "plus")]),
      el("div", { class: "data-list" }, users.map(user => el("article", { class: "user-row" },
        el("span", { class: "avatar", "aria-hidden": "true" }, user.display_name.slice(0, 1)),
        el("div", {},
          el("h3", {}, user.display_name, user.id === state.user.id ? ` (${t("me")})` : ""),
          el("p", {}, `${user.username} · ${user.active ? t("active") : t("inactive")}`),
          el("div", { class: "capabilities" }, user.capabilities.map(capability => badge(capabilityLabel(capability)))),
        ),
        el("div", { class: "actions" },
          button(t("managePermissions"), () => showUserForm(user), "secondary compact"),
          user.id !== state.user.id && button(t("deleteAccount"), () => showDeleteUser(user), "danger compact"),
        ),
      ))),
    );
  } catch (error) { if (main.isConnected) main.append(notice(errorText(error), "error", "alert")); }
}
function showDeleteUser(user) {
  const body = el("div", {},
    el("p", {}, ui("‘{name}’ ({username}) 계정을 삭제합니다.", { name: user.display_name, username: user.username })),
    notice(ui("이 계정은 즉시 로그인할 수 없게 됩니다. 이 계정의 로그인 정보만 삭제되며, 매뉴얼과 기존 질문 기록은 보존됩니다."), "warning"),
    el("div", { class: "dialog-footer" },
      button(ui("돌아가기"), () => dialog.close(), "secondary"),
      button(ui("계정 삭제"), async event => {
        await busy(event.currentTarget, async () => {
          try {
            await api(`/api/users/${encodeURIComponent(user.id)}`, { method: "DELETE" });
            dialog.close();
            renderShell();
            toast(ui("계정을 삭제했습니다. 매뉴얼과 기존 질문 기록은 보존됩니다."));
          } catch (error) { dialogError(body, error); }
        }, ui("삭제 중…"));
      }, "danger")));
  showDialog(ui("직원 계정 삭제"), body);
}
function showUserForm(user = null) {
  const username = input("staff-username", "text", { required: true, autocomplete: "off", minlength: 3, maxlength: 64, pattern: "[A-Za-z0-9_.\\-]+" });
  const displayName = input("staff-name", "text", { required: true, maxlength: 60 });
  const password = input("staff-password", "password", { required: true, minlength: 6, maxlength: 128, autocomplete: "new-password" });
  const capabilityChecks = Object.entries(capabilityKeys).map(([key, label]) => checkbox(`cap-${key}`, t(label), user ? user.capabilities.includes(key) : key === "ask"));
  const active = checkbox("staff-active", ui("계정 사용 허용"), user?.active ?? true);
  const submit = el("button", { type: "submit", class: "btn" }, user ? ui("권한 저장") : ui("계정 만들기"));
  const form = el("form", { class: "form-stack" },
    user ? el("p", {}, `${user.display_name} (${user.username})`) : el("div", { class: "form-grid" }, field(ui("아이디"), username), field(ui("이름"), displayName), el("div", { class: "full" }, field(ui("초기 비밀번호"), password, ui("6자 이상. 직원에게 별도 경로로 전달하세요.")))),
    el("fieldset", { class: "form-stack capability-fieldset" }, el("legend", {}, ui("접근 권한")), capabilityChecks),
    user && active,
    notice(ui("서비스 문서에는 수리·교정 절차가 포함될 수 있습니다. 해당 업무 담당자에게만 열람 권한을 부여하세요.")),
    el("div", { class: "dialog-footer" }, submit));
  form.addEventListener("submit", async event => {
    event.preventDefault(); await busy(submit, async () => {
      try {
        const capabilities = Object.keys(capabilityKeys).filter(key => form.querySelector(`#cap-${key}`).checked);
        if (user) await api(`/api/users/${encodeURIComponent(user.id)}`, { method: "PATCH", body: { capabilities, active: form.querySelector("#staff-active").checked } });
        else await api("/api/users", { method: "POST", body: { username: username.value.trim(), display_name: displayName.value.trim(), password: password.value, capabilities } });
        password.value = ""; dialog.close();
        const session = await api("/api/me"); state.user = session.user; state.csrf = session.csrf;
        renderShell(); toast(user ? ui("권한을 저장했습니다.") : ui("직원 계정을 만들었습니다."));
      } catch (error) {
        if (error.status === 401) { dialog.close(); renderAuth(false); toast(ui("권한 변경 후 다시 로그인해 주세요.")); }
        else dialogError(form, error);
      }
    });
  });
  showDialog(user ? ui("직원 권한 관리") : ui("직원 계정 추가"), form);
}

async function loadManualDefaults(container) {
  if (!container || !has("manage_documents")) return;
  const loadSession = state.sessionVersion;
  const loadUser = state.user?.id;
  const currentLoad = () => loadSession === state.sessionVersion && loadUser === state.user?.id && container.isConnected;
  try {
    const data = await api("/api/manual-defaults");
    if (!currentLoad()) return;
    state.manualDefaults = Array.isArray(data) ? data : [];
    const product = input("default-product", "search", { list: "default-products", placeholder: ui("제품 모델 검색"), required: true });
    const datalist = el("datalist", { id: "default-products" }, state.products.map(item => el("option", { value: item.id }, item.name)));
    const country = input("default-country", "text", { maxlength: 32, placeholder: ui("예: KOR, EUR"), required: true });
    const manual = el("select", { id: "default-document", required: true });
    const existing = el("div", { class: "default-manual-list" });
    const save = el("button", { type: "button", class: "btn" }, ui("기준 매뉴얼 저장"));
    const clear = button(ui("기준 해제"), async event => {
      const productId = product.value.trim(); const selectedCountry = country.value.trim();
      if (!productId || !selectedCountry) { toast(ui("제품과 대상 국가를 먼저 선택하세요.")); return; }
      await busy(event.currentTarget, async () => {
        try { await api(`/api/manual-defaults/${encodeURIComponent(productId)}`, { method: "PUT", body: { country: selectedCountry, document_id: "" } }); const refreshed = await api("/api/manual-defaults"); if (!currentLoad()) return; state.manualDefaults = Array.isArray(refreshed) ? refreshed : []; renderDefaults(); toast(ui("기준 매뉴얼을 해제했습니다.")); }
        catch (error) { toast(errorText(error)); }
      });
    }, "ghost compact");
    const renderDefaults = () => {
      const productId = product.value.trim();
      const docs = state.documents.filter(item => item.product_id === productId && eligibleSource(item) && ["user", "quick"].includes(item.kind) && item.country);
      manual.replaceChildren(el("option", { value: "" }, ui("기준 매뉴얼 선택")), ...docs.map(item => el("option", { value: item.id }, `${item.title} · ${item.country || ui("국가 미확인")} · ${item.revision || ui("개정 미지정")}`)));
      const current = manualDefault(productId);
      country.value = current?.country || ""; manual.value = current?.document_id || "";
      const rows = state.manualDefaults.filter(item => item.product_id === productId && item.active !== false);
      existing.replaceChildren(...rows.map(item => el("div", { class: "default-manual-row" },
        el("span", {}, `${item.country || ui("국가 미확인")} · ${state.documents.find(doc => doc.id === item.document_id)?.title || item.document_id}`),
        button(ui("기준 해제"), async event => {
          await busy(event.currentTarget, async () => {
            try {
              await api(`/api/manual-defaults/${encodeURIComponent(productId)}`, { method: "PUT", body: { country: item.country, document_id: "" } });
              const refreshed = await api("/api/manual-defaults"); if (!currentLoad()) return;
              state.manualDefaults = Array.isArray(refreshed) ? refreshed : []; renderDefaults(); toast(ui("기준 매뉴얼을 해제했습니다."));
            } catch (error) { toast(errorText(error)); }
          });
        }, "ghost compact"))));
    };
    product.addEventListener("input", renderDefaults);
    manual.addEventListener("change", () => {
      country.value = state.documents.find(item => item.id === manual.value)?.country || "";
    });
    save.addEventListener("click", async event => {
      const productId = product.value.trim(); const selectedCountry = country.value.trim();
      if (!productId || !selectedCountry || !manual.value) { toast(ui("제품·대상 국가·기준 매뉴얼을 모두 선택하세요.")); return; }
      await busy(event.currentTarget, async () => {
        try { await api(`/api/manual-defaults/${encodeURIComponent(productId)}`, { method: "PUT", body: { country: selectedCountry, document_id: manual.value } }); const refreshed = await api("/api/manual-defaults"); if (!currentLoad()) return; state.manualDefaults = Array.isArray(refreshed) ? refreshed : []; renderDefaults(); toast(ui("기준 매뉴얼을 저장했습니다.")); }
        catch (error) { toast(errorText(error)); }
      });
    });
    container.replaceChildren(el("h2", {}, ui("제품별 기준 매뉴얼")), el("p", {}, ui("관리자가 적용 국가와 기준 문서를 명시합니다. 높은 개정번호를 자동으로 최신본으로 선택하지 않습니다.")),
      el("div", { class: "form-grid default-manual-form" }, field(ui("제품"), product), datalist, field(ui("대상 국가 / 시장"), country), field(ui("기준 매뉴얼"), manual)),
      notice(ui("기준 매뉴얼 지정은 승인·최신·유효 상태를 확인하는 행위가 아닙니다. 문서 상태는 별도로 검토하세요."), "warning"), existing,
      el("div", { class: "actions" }, save, clear));
    renderDefaults();
  } catch (error) {
    if (error.status !== 404 && error.status !== 403 && container.isConnected) container.replaceChildren(notice(errorText(error), "warning"));
  }
}
function renderSettings(main) {
  const backupNeedsService = state.documents.some(document => document.kind === "service");
  const backupAllowed = has("manage_users") && (!backupNeedsService || has("read_service"));
  const backupButton = button(ui("백업 파일 다운로드"), downloadBackup, "secondary", "download");
  backupButton.disabled = !backupAllowed;
  const restoreButton = button(ui("복원 파일 선택"), showRestore, "secondary", "upload");
  restoreButton.disabled = !has("manage_users");
  const modelName = input("model-name", "text", { required: true, value: state.model?.name || "", placeholder: ui("이 PC에 설치된 모델 이름"), maxlength: 100, autocomplete: "off" });
  const enabled = checkbox("model-enabled", ui("로컬 AI 답변 사용"), Boolean(state.model?.enabled), ui("꺼두면 매뉴얼 검색만 제공합니다."));
  const save = el("button", { type: "submit", class: "btn" }, ui("모델 설정 저장"));
  const modelForm = el("form", { class: "form-stack" }, field(ui("로컬 모델 이름"), modelName, ui("Ollama에 설치된 모델 이름을 정확히 입력하세요. 외부 API나 클라우드 모델은 사용할 수 없습니다.")),
    enabled, el("div", { class: "actions" }, save, button(ui("연결 새로 확인"), async event => {
      await busy(event.currentTarget, async () => {
        try { state.model = await api("/api/model"); renderSettings(main); toast(ui("모델 상태를 갱신했습니다.")); }
        catch (error) { toast(errorText(error)); }
      });
    }, "secondary")));
  modelForm.addEventListener("submit", async event => {
    event.preventDefault(); await busy(save, async () => {
      try { state.model = await api("/api/model", { method: "POST", body: { enabled: modelForm.querySelector("#model-enabled").checked, model_name: modelName.value.trim() } }); renderShell(); toast(ui("모델 설정을 저장했습니다.")); }
      catch (error) { dialogError(modelForm, error); }
    });
  });
  main.replaceChildren(pageHeading(ui("운영 설정"), ui("이 PC의 로컬 AI와 자료 백업을 관리합니다.")),
    el("section", { class: "settings-section" }, el("h2", {}, ui("로컬 AI")),
      el("p", {}, ui("모든 답변 생성은 운영 PC에서 실행됩니다. 모델을 사용할 수 없으면 외부 서비스로 연결하지 않습니다.")),
      el("div", { class: "actions" }, badge(state.model?.ready ? ui("모델 연결됨") : ui("모델 준비 필요"), state.model?.ready ? "success" : "warning"), badge(state.model?.enabled ? ui("AI 답변 켜짐") : ui("근거 검색 모드"))),
      state.model?.detail && notice(ui(state.model.detail), state.model.ready ? "" : "warning"),
      state.model?.digest && el("p", { class: "form-help mono" }, ui("모델 확인값: {digest}", { digest: state.model.digest })), modelForm),
    has("manage_documents") && el("section", { class: "settings-section", id: "manual-defaults" }, el("p", { class: "muted" }, ui("기준 매뉴얼 설정을 불러오는 중입니다."))),
    el("section", { class: "settings-section" }, el("h2", {}, ui("자료 백업")),
      el("p", {}, ui("등록된 원문과 계정·문서 상태를 한 파일로 보관합니다. 백업에는 사내 자료가 포함되므로 안전한 위치에 보관하세요.")),
      !backupAllowed && notice(!has("manage_users") ? ui("전체 백업에는 계정 관리 권한도 필요합니다.") : ui("서비스 문서가 포함된 전체 백업에는 서비스 문서 열람 권한이 필요합니다."), "warning"),
      el("div", { class: "actions" }, backupButton)),
    el("section", { class: "settings-section" }, el("h2", {}, ui("백업에서 복원")),
      el("p", {}, ui("검증된 백업 파일로 이 프로그램의 데이터를 복원합니다. 복원 후에는 다시 로그인해야 합니다.")),
      el("p", { class: "form-help" }, ui("계정 관리 권한이 필요합니다. 서비스 문서가 들어 있는 백업은 해당 열람 권한도 필요합니다.")),
      restoreButton));
  loadManualDefaults(document.querySelector("#manual-defaults"));
}
async function downloadBackup(event) {
  await busy(event.currentTarget, async () => {
    try {
      const blob = await api("/api/backup", { method: "POST", blob: true });
      const url = URL.createObjectURL(blob); const anchor = el("a", { href: url, download: `bistos-backup-${new Date().toISOString().slice(0, 10)}.zip` });
      document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 10000); toast(ui("백업 파일 다운로드를 시작했습니다."));
    } catch (error) { toast(errorText(error)); }
  }, ui("백업 만드는 중…"));
}
function showRestore() {
  const file = input("restore-file", "file", { required: true, accept: ".zip" });
  const consent = checkbox("restore-confirm", ui("현재 자료를 이 백업의 내용으로 복원하는 것을 확인했습니다."));
  consent.querySelector("input").required = true;
  const submit = el("button", { type: "submit", class: "btn danger" }, ui("검증 후 복원"));
  const form = el("form", { class: "form-stack" }, notice(ui("복원하면 현재 데이터가 백업 시점으로 바뀝니다. 필요한 최신 자료를 먼저 백업하세요."), "warning"),
    field(ui("이 프로그램에서 만든 백업 ZIP"), file), consent, el("div", { class: "dialog-footer" }, submit));
  form.addEventListener("submit", async event => {
    event.preventDefault(); await busy(submit, async () => {
      try {
        const data = new FormData(); data.append("file", file.files[0]);
        await api("/api/restore", { method: "POST", body: data }); dialog.close(); state.currentJob = null; state.question = ""; renderAuth(false); toast(ui("복원했습니다. 백업에 포함된 계정으로 로그인하세요."));
      } catch (error) { dialogError(form, error); }
    }, ui("백업 검증·복원 중…"));
  });
  showDialog(ui("백업 복원"), form);
}

boot();
