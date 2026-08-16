"use strict";

const ROUTES = {
  overview: "Visão geral",
  cameras: "Câmeras",
  analytics: "Analytics",
  computers: "Computadores",
  network: "Rede",
  timeline: "Ocorrências",
  reports: "Relatórios",
  system: "Sistema",
};

const state = {
  route: "overview",
  overview: null,
  loading: false,
  error: null,
};

const content = document.getElementById("content");
const pageTitle = document.getElementById("page-title");
const refreshButton = document.getElementById("refresh-button");
const serviceBadge = document.getElementById("service-badge");
const connectionDot = document.getElementById("connection-dot");
const connectionLabel = document.getElementById("connection-label");
const lastUpdate = document.getElementById("last-update");
const topbarUpdate = document.getElementById("topbar-update");
const navButtons = [...document.querySelectorAll("[data-route]")];

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function statusBadge(status, label) {
  return element("span", `status-badge ${status || "unknown"}`, label || status || "desconhecido");
}

function statusLabel(status) {
  const labels = {
    active: "Ativo",
    current: "Atual",
    limited: "Limitado",
    warning: "Atenção",
    critical: "Crítico",
    stale: "Desatualizado",
    partial: "Parcial",
    unavailable: "Indisponível",
    not_configured: "Não configurado",
    waiting_for_data: "Aguardando dados",
    unknown: "Sem confirmação",
  };
  return labels[status] || status || "Desconhecido";
}

function formatNumber(value, suffix = "") {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "Sem dado";
  return `${Number(value).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}${suffix}`;
}

function formatDate(value) {
  if (!value) return "Sem coleta";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "medium" });
}

function moduleById(id) {
  return state.overview?.modules?.find((module) => module.id === id) || null;
}

function currentSnapshot() {
  return state.overview?.nvr?.snapshot || null;
}

function renderMetric(label, value, detail) {
  const metric = element("article", "metric");
  metric.append(element("span", "", label));
  metric.append(element("strong", "", value));
  if (detail) metric.append(element("small", "", detail));
  return metric;
}

function statusDescription(nvr) {
  if (!nvr) return "Estado do NVR indisponível.";
  if (nvr.state === "active") return "O snapshot do NVR está atual e disponível para consulta.";
  if (nvr.state === "stale") return "A última coleta do NVR está antiga; os valores abaixo podem estar desatualizados.";
  if (nvr.state === "unknown") return "O relógio do snapshot diverge do relógio local e exige verificação.";
  return "O Analytics está ativo, mas ainda não recebeu um snapshot válido do NVR.";
}

function renderOverview() {
  const overview = state.overview;
  const nvr = overview.nvr;
  const snapshot = nvr.snapshot;
  const metrics = snapshot?.metrics || {};
  const connectivity = metrics.camera_connectivity || {};
  const cameras = Object.values(connectivity);
  const online = cameras.filter((camera) => String(camera.status).toUpperCase() === "ONLINE").length;
  const recording = cameras.filter((camera) => camera.recording_active === true).length;

  const band = element("section", "summary-band");
  const main = element("div", `summary-main ${nvr.state}`);
  main.append(element("h2", "", "Estado operacional"));
  main.append(element("p", "", statusDescription(nvr)));
  const meta = element("div", "summary-meta");
  const list = element("dl");
  const rows = [
    ["NVR", snapshot?.overall_status || nvr.state],
    ["Coleta", formatDate(snapshot?.generated_at)],
    ["Modo", "Local e somente leitura"],
  ];
  rows.forEach(([key, value]) => {
    const row = element("div");
    row.append(element("dt", "", key), element("dd", "", value));
    list.append(row);
  });
  meta.append(list);
  band.append(main, meta);

  const metricsGrid = element("section", "metric-grid", undefined);
  metricsGrid.append(
    renderMetric("Câmeras online", cameras.length ? `${online} de ${cameras.length}` : "Sem dado", recording ? `${recording} gravando` : "Sem gravação medida"),
    renderMetric("HD de gravação", metrics.hd_available === true ? "Disponível" : metrics.hd_available === false ? "Indisponível" : "Sem dado", formatNumber(metrics.hd_free_gb, " GB livres")),
    renderMetric("Memória do NVR", formatNumber(metrics.process_memory_mb, " MB"), formatNumber(metrics.thread_count, " threads")),
    renderMetric("Backups pendentes", formatNumber(metrics.pending_backup_count), formatNumber(metrics.pending_backup_gb, " GB")),
  );

  const issuesSection = element("section", "section-block");
  const heading = element("div", "section-heading");
  heading.append(element("h2", "", "Ocorrências atuais"), element("p", "", `${snapshot?.issues?.length || 0} registro(s)`));
  issuesSection.append(heading);
  if (!snapshot?.issues?.length) {
    issuesSection.append(element("div", "empty-state", "Nenhuma ocorrência disponível na coleta atual."));
  } else {
    issuesSection.append(renderIssuesTable(snapshot.issues));
  }

  content.append(band, metricsGrid, issuesSection);
}

function renderIssuesTable(issues) {
  const table = element("table", "data-table");
  const thead = element("thead");
  const header = element("tr");
  ["Nível", "Ocorrência", "Ação"].forEach((label) => header.append(element("th", "", label)));
  thead.append(header);
  const tbody = element("tbody");
  issues.forEach((issue) => {
    const row = element("tr");
    const statusCell = element("td");
    statusCell.append(statusBadge(issue.severity, issue.severity));
    row.append(statusCell, element("td", "", issue.summary || issue.code), element("td", "", issue.action || "Revisar no NVR"));
    tbody.append(row);
  });
  table.append(thead, tbody);
  return table;
}

function renderCameras() {
  const toolbar = element("div", "camera-toolbar");
  toolbar.append(element("p", "", "Visualização direta do go2rtc; o Analytics não retransmite nem grava este vídeo."));
  const link = element("a", "text-link", "Abrir em nova janela");
  link.href = state.overview.links.cameras;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  toolbar.append(link);

  const frame = element("iframe", "camera-frame");
  frame.src = state.overview.links.cameras;
  frame.title = "Painel ao vivo das câmeras";
  frame.loading = "eager";
  frame.allow = "autoplay; fullscreen";
  frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms allow-popups");
  content.append(toolbar, frame);
}

function renderAnalytics() {
  const module = moduleById("analytics");
  const snapshot = currentSnapshot();
  const intelligence = snapshot?.intelligence;

  const band = element("section", "summary-band");
  const main = element("div", "summary-main active");
  main.append(element("h2", "", "Fundação Analytics"));
  main.append(element("p", "", module?.detail || "Serviço local ativo."));
  const meta = element("div", "summary-meta");
  meta.append(statusBadge(module?.status, statusLabel(module?.status)));
  band.append(main, meta);
  content.append(band);

  if (!intelligence) {
    const empty = element("div", "empty-state");
    empty.append(element("h2", "", "Sem análise operacional atual"));
    empty.append(element("p", "", "A fundação está ativa. Visão computacional, agentes Windows e fontes de rede permanecem desativados até configuração e validação próprias."));
    content.append(empty);
    return;
  }
  const section = element("section", "section-block");
  const heading = element("div", "section-heading");
  heading.append(element("h2", "", intelligence.headline || "Análise do NVR"));
  section.append(heading);
  const table = element("table", "data-table");
  const body = element("tbody");
  const protection = intelligence.hardware_protection || {};
  const recommendationLabels = {
    continue_monitoring: "Continuar monitorando",
    safe_stop: "Parada segura recomendada",
  };
  [
    ["Resumo", intelligence.explanation],
    ["Confiança", formatNumber(intelligence.confidence_score, "%")],
    ["Gravação", recommendationLabels[protection.recording_recommendation] || protection.recording_recommendation],
    ["Manutenção pesada", protection.heavy_maintenance_allowed === true ? "Liberada" : protection.heavy_maintenance_allowed === false ? "Bloqueada" : null],
    ["Proteção de hardware", protection.reason],
  ].forEach(([label, value]) => {
    const row = element("tr");
    row.append(element("th", "", label), element("td", "", value || "Sem dado"));
    body.append(row);
  });
  table.append(body);
  section.append(table);
  if (Array.isArray(intelligence.priority_actions) && intelligence.priority_actions.length) {
    const actionsHeading = element("h3", "subsection-title", "Ações prioritárias");
    const actions = element("ol", "priority-list");
    intelligence.priority_actions.forEach((action) => actions.append(element("li", "", action)));
    section.append(actionsHeading, actions);
  }
  content.append(section);
}

function renderPendingModule(moduleId, title, body) {
  const module = moduleById(moduleId);
  const empty = element("div", "empty-state");
  const heading = element("div", "section-heading");
  heading.append(element("h2", "", title), statusBadge(module?.status, module?.status));
  empty.append(heading, element("p", "", body));
  content.append(empty);
}

function formatAddresses(values) {
  return Array.isArray(values) && values.length ? values.join(", ") : "Não configurado";
}

function renderNetwork() {
  const network = state.overview.network;
  if (!network || network.state !== "active") {
    renderPendingModule(
      "network",
      "Diagnóstico de rede indisponível",
      "O painel não recebeu uma configuração válida dos adaptadores deste computador.",
    );
    return;
  }

  const connectivity = network.connectivity || {};
  const interfaces = network.interfaces || [];
  const band = element("section", "summary-band");
  const main = element("div", "summary-main limited");
  main.append(element("h2", "", "Cobertura deste computador"));
  main.append(
    element(
      "p",
      "",
      "Adaptadores, rota padrão e DNS são lidos do Windows. Tráfego dos demais dispositivos da loja não é observado por este coletor.",
    ),
  );
  const meta = element("div", "summary-meta");
  const list = element("dl");
  [
    ["Estado", "Conectado"],
    ["Cobertura", "Somente este PC"],
    ["Coleta", formatDate(network.collected_at)],
  ].forEach(([key, value]) => {
    const row = element("div");
    row.append(element("dt", "", key), element("dd", "", value));
    list.append(row);
  });
  meta.append(list);
  band.append(main, meta);

  const metrics = element("section", "metric-grid");
  metrics.append(
    renderMetric("Interfaces ativas", formatNumber(connectivity.active_interface_count), "Configuração do Windows"),
    renderMetric("Gateway padrão", connectivity.default_gateway_configured ? "Configurado" : "Ausente", "Rota local"),
    renderMetric("Servidores DNS", connectivity.dns_configured ? "Configurados" : "Ausentes", "Sem captura de consultas"),
    renderMetric("Tráfego da loja", network.can_observe_store_traffic ? "Disponível" : "Não observado", "Exige fonte externa"),
  );

  const section = element("section", "section-block");
  const heading = element("div", "section-heading");
  heading.append(element("h2", "", "Interfaces do Windows"), element("p", "", `${interfaces.length} ativa(s)`));
  const table = element("table", "data-table");
  const thead = element("thead");
  const header = element("tr");
  ["Interface", "Link", "IPv4", "Gateway", "DNS"].forEach((label) => header.append(element("th", "", label)));
  thead.append(header);
  const tbody = element("tbody");
  interfaces.forEach((networkInterface) => {
    const row = element("tr");
    row.append(
      element("td", "", networkInterface.alias),
      element("td", "", networkInterface.link_speed || "Sem dado"),
      element("td", "", formatAddresses(networkInterface.ipv4)),
      element("td", "", formatAddresses(networkInterface.gateways)),
      element("td", "", formatAddresses(networkInterface.dns_servers)),
    );
    tbody.append(row);
  });
  table.append(thead, tbody);
  section.append(heading, table);

  const boundary = element("section", "network-boundary");
  boundary.append(element("strong", "", "Limite de visibilidade"));
  boundary.append(
    element(
      "p",
      "",
      "Para indicadores da rede inteira será necessária uma fonte autorizada no gateway, como DNS agregado ou NetFlow/IPFIX. O painel não captura mensagens, senhas, páginas ou pacotes.",
    ),
  );
  content.append(band, metrics, section, boundary);
}

function renderTimeline() {
  const issues = currentSnapshot()?.issues || [];
  const snapshotCurrent = state.overview.nvr?.state === "active";
  if (!issues.length) {
    renderPendingModule(
      "reports",
      snapshotCurrent ? "Nenhuma ocorrência atual" : "Nenhuma ocorrência na última coleta",
      snapshotCurrent
        ? "O snapshot atual não contém alertas. O histórico persistente ainda não está configurado."
        : "A última coleta recebida não contém alertas, mas está desatualizada. O histórico persistente ainda não está configurado.",
    );
    return;
  }
  const section = element("section", "section-block");
  const heading = element("div", "section-heading");
  heading.append(
    element("h2", "", snapshotCurrent ? "Ocorrências atuais" : "Ocorrências da última coleta"),
    element("p", "", snapshotCurrent ? "Snapshot atual, sem histórico persistente" : "Snapshot desatualizado, sem histórico persistente"),
  );
  const list = element("ol", "timeline-list");
  issues.forEach((issue) => {
    const item = element("li");
    item.append(element("h3", "", issue.summary || issue.code), element("p", "", issue.action || "Revisar no NVR"));
    list.append(item);
  });
  section.append(heading, list);
  content.append(section);
}

function renderReports() {
  const operations = state.overview.operations || {};
  const report = operations.report;
  if (!report) {
    renderPendingModule("reports", "Relatório indisponível", "Nenhuma coleta operacional válida foi recebida.");
    return;
  }

  const band = element("section", "summary-band");
  const main = element("div", `summary-main ${report.state}`);
  main.append(element("h2", "", report.headline));
  main.append(
    element(
      "p",
      "",
      report.state === "current"
        ? "Consolidação da coleta atual do NVR e deste computador."
        : "Os dados abaixo são parciais e não devem ser usados como histórico diário.",
    ),
  );
  const meta = element("div", "summary-meta");
  const list = element("dl");
  [
    ["Estado", statusLabel(report.state)],
    ["Gerado", formatDate(report.generated_at)],
    ["Fonte NVR", formatDate(report.source_generated_at)],
    ["Escopo", "NVR e este PC"],
  ].forEach(([key, value]) => {
    const row = element("div");
    row.append(element("dt", "", key), element("dd", "", value));
    list.append(row);
  });
  meta.append(list);
  band.append(main, meta);

  const section = element("section", "section-block");
  const heading = element("div", "section-heading");
  heading.append(element("h2", "", "Verificações desta coleta"), element("p", "", `${report.checks.length} sinais`));
  const table = element("table", "data-table report-table");
  const thead = element("thead");
  const header = element("tr");
  ["Estado", "Verificação", "Resultado", "Evidência"].forEach((label) => header.append(element("th", "", label)));
  thead.append(header);
  const tbody = element("tbody");
  report.checks.forEach((check) => {
    const row = element("tr");
    const stateCell = element("td");
    stateCell.append(statusBadge(check.status, statusLabel(check.status)));
    const detailCell = element("td", "check-copy");
    detailCell.append(element("strong", "", check.label), element("small", "", check.detail));
    row.append(
      stateCell,
      detailCell,
      element("td", "", check.value),
      element("td", "evidence", check.evidence),
    );
    tbody.append(row);
  });
  table.append(thead, tbody);
  section.append(heading, table);
  content.append(band, section);
}

function renderFindingList(title, items, kind) {
  const section = element("section", `finding-section ${kind}`);
  const heading = element("div", "section-heading");
  heading.append(element("h2", "", title), element("p", "", `${items.length} item(ns)`));
  const list = element("div", "finding-list");
  items.forEach((item) => {
    const row = element("article", "finding-row");
    row.append(element("strong", "", item.label), element("p", "", item.detail));
    row.append(element("code", "evidence", item.evidence));
    list.append(row);
  });
  section.append(heading, list);
  return section;
}

function renderSystem() {
  const operations = state.overview.operations || {};
  const readiness = operations.readiness || {
    status: "unavailable",
    generated_at: null,
    strengths: [],
    limitations: [],
  };
  const band = element("section", "summary-band");
  const main = element("div", `summary-main ${readiness.status}`);
  main.append(element("h2", "", "Prontidão operacional"));
  main.append(
    element(
      "p",
      "",
      "Leitura objetiva do que está protegido, do que possui dados e do que ainda não foi configurado.",
    ),
  );
  const meta = element("div", "summary-meta");
  const metaList = element("dl");
  [
    ["Estado", statusLabel(readiness.status)],
    ["Pontos fortes", readiness.strengths.length],
    ["Limitações", readiness.limitations.length],
    ["Coleta", formatDate(readiness.generated_at)],
  ].forEach(([key, value]) => {
    const row = element("div");
    row.append(element("dt", "", key), element("dd", "", value));
    metaList.append(row);
  });
  meta.append(metaList);
  band.append(main, meta);

  const findings = element("div", "finding-columns");
  findings.append(
    renderFindingList("Pontos fortes", readiness.strengths, "strengths"),
    renderFindingList("Limitações e riscos", readiness.limitations, "limitations"),
  );

  const heading = element("div", "section-heading");
  heading.append(element("h2", "", "Componentes"), element("p", "", "Estado declarado pelas fontes locais"));
  const list = element("div", "module-list");
  state.overview.modules.forEach((module) => {
    const row = element("div", "module-row");
    row.append(
      element("strong", "", module.label),
      statusBadge(module.status, statusLabel(module.status)),
      element("p", "", module.detail),
    );
    list.append(row);
  });
  const modules = element("section", "section-block");
  modules.append(heading, list);
  content.append(band, findings, modules);
}

function render() {
  clear(content);
  content.setAttribute("aria-busy", state.loading ? "true" : "false");
  pageTitle.textContent = ROUTES[state.route];
  navButtons.forEach((button) => button.classList.toggle("active", button.dataset.route === state.route));

  if (state.loading && !state.overview) {
    content.append(element("div", "loading-state", "Carregando estado operacional..."));
    return;
  }
  if (state.error && !state.overview) {
    const error = element("div", "error-state");
    error.append(element("h2", "", "Painel indisponível"), element("p", "", state.error));
    content.append(error);
    return;
  }
  if (!state.overview) return;

  if (state.error) {
    const warning = element("div", "stale-data-banner");
    warning.setAttribute("role", "status");
    warning.append(
      element("strong", "", "Atualização falhou"),
      element("span", "", `${state.error} Exibindo os últimos dados recebidos.`),
    );
    content.append(warning);
  }

  if (state.route === "overview") renderOverview();
  if (state.route === "cameras") renderCameras();
  if (state.route === "analytics") renderAnalytics();
  if (state.route === "computers") renderPendingModule("computers", "Computadores não configurados", "O agente Windows ainda não foi instalado. Nenhum aplicativo, janela, tecla ou conteúdo está sendo coletado.");
  if (state.route === "network") renderNetwork();
  if (state.route === "timeline") renderTimeline();
  if (state.route === "reports") renderReports();
  if (state.route === "system") renderSystem();
}

function updateConnection(ready, overview) {
  const serviceStatus = ready ? overview?.service?.status || "active" : "unavailable";
  const apiGeneratedAt = overview?.generated_at;
  const nvrGeneratedAt = overview?.nvr?.snapshot?.generated_at;
  connectionDot.className = `status-dot ${serviceStatus}`;
  connectionLabel.textContent = ready ? "Serviço local ativo" : "Serviço indisponível";
  serviceBadge.className = `status-badge ${serviceStatus}`;
  serviceBadge.textContent = ready ? "Local · ativo" : "Indisponível";
  lastUpdate.textContent = overview
    ? `API ${formatDate(apiGeneratedAt)} · NVR ${formatDate(nvrGeneratedAt)}`
    : "Falha na atualização";
  topbarUpdate.textContent = `NVR ${formatDate(nvrGeneratedAt)}`;
}

async function loadOverview() {
  if (state.loading) return;
  state.loading = true;
  state.error = null;
  refreshButton.disabled = true;
  render();

  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch("/api/v1/overview", {
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`Resposta HTTP ${response.status}`);
    state.overview = await response.json();
    updateConnection(true, state.overview);
  } catch (error) {
    state.error = error.name === "AbortError" ? "A API local não respondeu em 5 segundos." : "Não foi possível consultar a API local.";
    updateConnection(false, state.overview);
  } finally {
    window.clearTimeout(timeout);
    state.loading = false;
    refreshButton.disabled = false;
    render();
  }
}

function keepActiveRouteVisible() {
  const activeButton = navButtons.find((button) => button.dataset.route === state.route);
  const navigation = activeButton?.parentElement;
  if (!activeButton || !navigation) return;
  const target = activeButton.offsetLeft - (navigation.clientWidth - activeButton.clientWidth) / 2;
  navigation.scrollLeft = Math.max(0, target);
}

function selectRoute(route, updateHash = true) {
  if (!ROUTES[route]) route = "overview";
  state.route = route;
  if (updateHash && window.location.hash !== `#/${route}`) window.location.hash = `#/${route}`;
  window.scrollTo(0, 0);
  render();
  keepActiveRouteVisible();
}

navButtons.forEach((button) => button.addEventListener("click", () => selectRoute(button.dataset.route)));
refreshButton.addEventListener("click", loadOverview);
window.addEventListener("hashchange", () => selectRoute(window.location.hash.replace(/^#\/?/, ""), false));
window.addEventListener("resize", keepActiveRouteVisible);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) loadOverview();
});

selectRoute(window.location.hash.replace(/^#\/?/, "") || "overview", false);
loadOverview();
window.setInterval(() => {
  if (!document.hidden && state.route !== "cameras") loadOverview();
}, 15000);
