(() => {
const form = document.getElementById("run-form");
const progressPanel = document.getElementById("run-progress");
const progressFill = document.getElementById("run-progress-fill");
const progressPercent = document.getElementById("run-progress-percent");
const progressStage = document.getElementById("run-progress-stage");
const progressElapsed = document.getElementById("run-progress-elapsed");
const archiveInput = document.getElementById("project_archive");
const archiveFileName = document.getElementById("archive-file-name");
const archiveZone = archiveInput ? archiveInput.closest(".upload-zone") : null;
const progressStages = [
  [8, "Подготовка запуска"],
  [22, "Загрузка файлов"],
  [42, "Извлечение сущностей"],
  [62, "Проверка ссылок и файлов"],
  [82, "Проверка фактов и версий"],
  [96, "Сборка отчёта"],
  [100, "Отчёт готов"]
];
let progressTimer = null;
let progressStartedAt = 0;
let progressValue = 0;

function progressLabel(value) {
  for (const item of progressStages) {
    if (value <= item[0]) return item[1];
  }
  return "Сборка отчёта";
}

function setProgress(value, label) {
  progressValue = Math.max(0, Math.min(100, Math.round(value)));
  if (progressFill) progressFill.style.width = `${progressValue}%`;
  if (progressPercent) progressPercent.textContent = `${progressValue}%`;
  if (progressStage) progressStage.textContent = label || progressLabel(progressValue);
}

function startProgress() {
  if (!progressPanel) return;
  progressPanel.hidden = false;
  progressPanel.classList.remove("is-error");
  progressPanel.setAttribute("aria-busy", "true");
  progressStartedAt = Date.now();
  setProgress(3, "Подготовка запуска");
  if (progressTimer) window.clearInterval(progressTimer);
  progressTimer = window.setInterval(() => {
    const elapsedSeconds = Math.max(0, Math.floor((Date.now() - progressStartedAt) / 1000));
    const nextValue = Math.min(94, 3 + Math.log2(elapsedSeconds + 1) * 18);
    setProgress(nextValue);
    if (progressElapsed) progressElapsed.textContent = `${elapsedSeconds} с`;
  }, 700);
}

function stopProgress(value, label) {
  if (progressTimer) window.clearInterval(progressTimer);
  progressTimer = null;
  if (progressPanel) progressPanel.setAttribute("aria-busy", "false");
  setProgress(value, label);
}

if (form) {
  form.addEventListener("submit", async (event) => {
    if (form.dataset.submitting === "1") {
      event.preventDefault();
      return;
    }
    if (!window.fetch) return;
    event.preventDefault();
    form.dataset.submitting = "1";
    form.classList.add("loading");
    const button = form.querySelector("button[type='submit']");
    if (button) {
      button.disabled = true;
      button.textContent = "Проверяю...";
    }
    startProgress();

    try {
      const payload = new FormData(form);
      const response = await fetch(form.action, {
        method: "POST",
        body: payload
      });
      const html = await response.text();
      stopProgress(100, response.ok ? "Отчёт готов" : "Проверка завершилась с ошибкой");
      window.setTimeout(() => {
        document.open();
        document.write(html);
        document.close();
      }, 250);
    } catch (error) {
      stopProgress(progressValue, "Не удалось получить ответ");
      if (progressPanel) progressPanel.classList.add("is-error");
      form.classList.remove("loading");
      delete form.dataset.submitting;
      if (button) {
        button.disabled = false;
        button.textContent = "Запустить";
      }
    }
  });
}

if (archiveInput && archiveFileName) {
  archiveInput.addEventListener("change", () => {
    const file = archiveInput.files && archiveInput.files[0];
    archiveFileName.textContent = file ? file.name : "ZIP / RAR / TAR";
  });
}

if (archiveZone) {
  archiveZone.addEventListener("dragenter", () => archiveZone.classList.add("is-dragging"));
  archiveZone.addEventListener("dragleave", () => archiveZone.classList.remove("is-dragging"));
  archiveZone.addEventListener("drop", () => archiveZone.classList.remove("is-dragging"));
}

const restart = document.querySelector(".run-restart");
if (restart && form) {
  const submitCurrentForm = (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (form.requestSubmit) form.requestSubmit();
    else form.submit();
  };
  restart.addEventListener("click", submitCurrentForm);
  restart.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") submitCurrentForm(event);
  });
}

const diagnostics = document.querySelector(".diagnostics");
if (diagnostics) diagnostics.removeAttribute("open");

const table = document.getElementById("findings-table");
const hideUnknown = document.getElementById("flt-hide-unknown");
const criterionButtons = document.querySelectorAll("[data-criterion-filter]");
const severityButtons = document.querySelectorAll("[data-severity-filter]");
const activeCriterionLabel = document.getElementById("active-criterion-label");
const activeSeverityLabel = document.getElementById("active-severity-label");
const activeColumnFilterLabel = document.getElementById("active-column-filter-label");
const resultCount = document.getElementById("filter-result-count");
const columnFilterButtons = table ? Array.from(table.querySelectorAll("[data-column-filter]")) : [];
const columnFilterState = new Map();
let activeCriterion = "all";
let activeSeverity = "all";
let activeColumnMenu = null;

function rowValue(row, columnIndex) {
  const cell = row.cells[columnIndex];
  if (!cell) return "";
  return cell.textContent.replace(/\\s+/g, " ").trim();
}

function valueLabel(value) {
  return value || "Пусто";
}

function sortedColumnValues(columnIndex) {
  if (!table) return [];
  const values = new Set();
  table.querySelectorAll("tbody tr.frow").forEach((row) => values.add(rowValue(row, columnIndex)));
  return Array.from(values).sort((left, right) => valueLabel(left).localeCompare(valueLabel(right), "ru"));
}

function closeColumnMenu() {
  if (activeColumnMenu) activeColumnMenu.remove();
  activeColumnMenu = null;
  columnFilterButtons.forEach((button) => button.setAttribute("aria-expanded", "false"));
}

function updateColumnFilterState(columnIndex, values, checkedValues) {
  if (checkedValues.length === values.length) {
    columnFilterState.delete(columnIndex);
  } else {
    columnFilterState.set(columnIndex, new Set(checkedValues));
  }
  columnFilterButtons.forEach((button, index) => {
    button.classList.toggle("is-active", columnFilterState.has(index));
  });
  if (activeColumnFilterLabel) {
    const activeCount = columnFilterState.size;
    activeColumnFilterLabel.textContent = activeCount ? `Колонки: ${activeCount}` : "Колонки: нет";
  }
  applyFilters();
}

function buildColumnMenu(button, columnIndex) {
  closeColumnMenu();
  const values = sortedColumnValues(columnIndex);
  const selected = columnFilterState.get(columnIndex);
  const menu = document.createElement("div");
  menu.className = "column-filter-menu";
  menu.setAttribute("role", "dialog");

  const head = document.createElement("div");
  head.className = "column-filter-head";
  const title = document.createElement("span");
  title.textContent = button.dataset.columnLabel || "Колонка";
  const clear = document.createElement("button");
  clear.type = "button";
  clear.className = "column-filter-clear";
  clear.textContent = "Сбросить";
  clear.addEventListener("click", (event) => {
    event.stopPropagation();
    columnFilterState.delete(columnIndex);
    closeColumnMenu();
    columnFilterButtons.forEach((item, index) => item.classList.toggle("is-active", columnFilterState.has(index)));
    if (activeColumnFilterLabel) {
      const activeCount = columnFilterState.size;
      activeColumnFilterLabel.textContent = activeCount ? `Колонки: ${activeCount}` : "Колонки: нет";
    }
    applyFilters();
  });
  head.append(title, clear);
  menu.append(head);

  const list = document.createElement("div");
  list.className = "column-filter-options";
  if (values.length === 0) {
    const empty = document.createElement("div");
    empty.className = "column-filter-empty";
    empty.textContent = "Нет значений";
    list.append(empty);
  } else {
    values.forEach((value) => {
      const option = document.createElement("label");
      option.className = "column-filter-option";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = value;
      checkbox.checked = selected ? selected.has(value) : true;
      const caption = document.createElement("span");
      caption.className = "column-filter-value";
      caption.textContent = valueLabel(value);
      checkbox.addEventListener("change", () => {
        const checkedValues = Array.from(list.querySelectorAll("input[type='checkbox']:checked")).map((item) => item.value);
        updateColumnFilterState(columnIndex, values, checkedValues);
      });
      option.append(checkbox, caption);
      list.append(option);
    });
  }
  menu.append(list);

  document.body.append(menu);
  const rect = button.getBoundingClientRect();
  const left = Math.min(Math.max(12, rect.left), window.innerWidth - menu.offsetWidth - 12);
  menu.style.left = `${left}px`;
  menu.style.top = `${rect.bottom + 8}px`;
  button.setAttribute("aria-expanded", "true");
  activeColumnMenu = menu;
}

function updateEmptyState() {
  if (!table) return;
  const rows = table.querySelectorAll("tbody tr.frow");
  let visible = 0;
  rows.forEach((row) => {
    if (getComputedStyle(row).display !== "none") visible += 1;
  });
  const note = document.getElementById("no-match");
  if (note) note.style.display = rows.length > 0 && visible === 0 ? "" : "none";
  if (resultCount) resultCount.textContent = `видно: ${visible} из ${rows.length}`;
}

function applyFilters() {
  if (!table) return;
  table.classList.toggle("hide-unknown", !!(hideUnknown && hideUnknown.checked));
  const rows = table.querySelectorAll("tbody tr.frow");
  rows.forEach((row) => {
    const byCriterion = activeCriterion === "all" || row.dataset.criterion === activeCriterion;
    const bySeverity = activeSeverity === "all" || row.dataset.severity === activeSeverity;
    const byColumns = Array.from(columnFilterState.entries()).every(([columnIndex, selected]) => selected.has(rowValue(row, columnIndex)));
    row.style.display = byCriterion && bySeverity && byColumns ? "" : "none";
  });
  updateEmptyState();
}

criterionButtons.forEach((button) => {
  button.addEventListener("click", () => {
    activeCriterion = button.dataset.criterionFilter || "all";
    criterionButtons.forEach((item) => item.classList.toggle("is-active", item === button));
    if (activeCriterionLabel) {
      const label = button.dataset.criterionLabel || "все";
      activeCriterionLabel.textContent = `Критерий: ${label}`;
    }
    applyFilters();
    const findings = document.getElementById("findings");
    if (findings) findings.scrollIntoView({ behavior: "smooth", block: "start" });
  });
});

severityButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const nextSeverity = button.dataset.severityFilter || "all";
    activeSeverity = activeSeverity === nextSeverity ? "all" : nextSeverity;
    severityButtons.forEach((item) => item.classList.toggle("is-active", activeSeverity !== "all" && item === button));
    if (activeSeverityLabel) {
      const label = activeSeverity === "all" ? "все" : button.dataset.severityLabel || nextSeverity;
      activeSeverityLabel.textContent = `Критичность: ${label}`;
    }
    applyFilters();
    const findings = document.getElementById("findings");
    if (findings) findings.scrollIntoView({ behavior: "smooth", block: "start" });
  });
});

columnFilterButtons.forEach((button, columnIndex) => {
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (button.getAttribute("aria-expanded") === "true") {
      closeColumnMenu();
      return;
    }
    buildColumnMenu(button, columnIndex);
  });
});

document.addEventListener("click", (event) => {
  if (activeColumnMenu && !activeColumnMenu.contains(event.target)) closeColumnMenu();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeColumnMenu();
});

window.addEventListener("resize", closeColumnMenu);
if (hideUnknown) hideUnknown.addEventListener("change", applyFilters);
applyFilters();
})();
