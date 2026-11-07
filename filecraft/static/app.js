"use strict";

const $ = (id) => document.getElementById(id);
const formats = JSON.parse($("format-data").textContent);
const token = document.querySelector('meta[name="filecraft-token"]').content;
const names = { pdf: "PDF", jpg: "JPG", png: "PNG", webp: "WebP", xlsx: "Excel (.xlsx)", csv: "CSV", tsv: "TSV", txt: "Plain text (.txt)" };
const images = ["jpg", "jpeg", "png", "webp"];
const maxUpload = 64 * 1024 * 1024;
const allFormats = $("files").accept;
const tabs = [...document.querySelectorAll('[role="tab"]')];
const shortcuts = [...document.querySelectorAll("[data-preset]")];
const resultUrls = new Map();
let files = [];
let preset = null;
let busy = false;

function selectTab(selected) {
  for (const tab of tabs) {
    const active = tab === selected;
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
    $(tab.getAttribute("aria-controls")).hidden = !active;
  }
}

for (const tab of tabs) {
  tab.addEventListener("click", () => selectTab(tab));
  tab.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? tabs[0] : event.key === "End" ? tabs.at(-1) : tabs.find((item) => item !== tab);
    selectTab(next);
    next.focus();
  });
}

function extension(file) {
  return file?.name.split(".").at(-1).toLowerCase() || "";
}

function sizeLabel(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function clearFeedback(tool) {
  if (resultUrls.has(tool)) {
    URL.revokeObjectURL(resultUrls.get(tool));
    resultUrls.delete(tool);
  }
  const feedback = $(`${tool}-feedback`);
  feedback.hidden = true;
  feedback.className = "feedback";
  feedback.replaceChildren();
  return feedback;
}

function showFeedback(tool, message, kind = "") {
  const feedback = clearFeedback(tool);
  if (kind) feedback.classList.add(kind);
  feedback.textContent = message;
  feedback.hidden = false;
  return feedback;
}

function syncSettings() {
  const ext = extension(files[0]);
  const out = $("target").value;
  $("dpi-field").hidden = !(ext === "pdf" && ["jpg", "png"].includes(out));
  $("convert-button").disabled = busy || !out;
  $("files").disabled = busy;
  $("target").disabled = busy || !out;
  $("dpi").disabled = busy;
  $("media-button").disabled = busy;
  for (const button of shortcuts) button.disabled = busy;
  for (const button of $("file-list").querySelectorAll("button")) button.disabled = busy;
  for (const field of $("media-form").querySelectorAll("input")) field.disabled = busy;

  let note = "";
  if (ext === "pdf" && out === "xlsx") note = "Each detected table with borders becomes a worksheet. Scanned PDFs need OCR.";
  if (ext === "pdf" && out === "txt") note = "Extracts selectable text. Scanned PDFs need OCR.";
  if (out === "jpg") note = "JPG uses maximum quality. Choose PNG for lossless output.";
  if (out === "png" && ext === "pdf") note = "PNG preserves the rendered pixels. More DPI creates larger files.";
  if (out === "pdf") note = "Images keep their pixel dimensions. Multiple images become pages in the order shown.";
  if (out === "csv" || out === "tsv") note = "Exports cached worksheet values. Multiple worksheets come as a ZIP.";
  $("quality-note").hidden = !note;
  $("quality-note").textContent = note;
}

function updateFiles() {
  const list = $("file-list");
  const previous = $("target").value;
  list.replaceChildren();
  list.hidden = !files.length;
  files.forEach((file, index) => {
    const item = document.createElement("li");
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = file.name;
    name.title = file.name;
    const size = document.createElement("span");
    size.className = "size";
    size.textContent = sizeLabel(file.size);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Remove ${file.name}`);
    remove.addEventListener("click", () => {
      files.splice(index, 1);
      clearFeedback("convert");
      updateFiles();
      ($("file-list").querySelector("button") || $("files")).focus();
    });
    item.append(name, size, remove);
    list.append(item);
  });
  const choices = files.length > 1 ? ["pdf"] : (formats[extension(files[0])] || []);
  $("target").replaceChildren(...(choices.length ? choices.map((ext) => new Option(names[ext], ext)) : [new Option("Select a file first", "")]));
  const preferred = preset?.output || previous;
  if (choices.includes(preferred)) $("target").value = preferred;
  for (const shortcut of shortcuts) shortcut.setAttribute("aria-pressed", String(shortcut.dataset.preset === preset?.key));
  $("file-hint").textContent = files.length > 1
    ? `${files.length} images selected · ${sizeLabel(files.reduce((sum, file) => sum + file.size, 0))} total`
    : "PDF, JPG, PNG, WebP, XLSX, CSV, TSV · 64 MB total";
  syncSettings();
}

function pickFiles(incoming) {
  if (!incoming.length || busy) return;
  clearFeedback("convert");
  if (incoming.length > 20 || incoming.reduce((sum, file) => sum + file.size, 0) >= maxUpload) {
    showFeedback("convert", "Choose up to 20 files totaling less than 64 MB.", "error");
    return;
  }
  if (incoming.some((file) => !Object.hasOwn(formats, extension(file)))) {
    showFeedback("convert", "Choose a PDF, JPG, PNG, WebP, XLSX, CSV, or TSV file.", "error");
    return;
  }
  if (incoming.length > 1 && incoming.some((file) => !images.includes(extension(file)))) {
    showFeedback("convert", "Choose one file, or select only images to combine into a PDF.", "error");
    return;
  }
  if (preset && incoming.some((file) => !preset.inputs.includes(extension(file)))) preset = null;
  files = incoming;
  updateFiles();
}

$("files").addEventListener("change", (event) => {
  pickFiles([...event.currentTarget.files]);
  event.currentTarget.value = "";
  event.currentTarget.accept = allFormats;
});

for (const type of ["dragenter", "dragover", "dragleave", "drop"]) {
  $("drop-zone").addEventListener(type, (event) => {
    event.preventDefault();
    $("drop-zone").classList.toggle("over", !busy && ["dragenter", "dragover"].includes(type));
    if (type === "drop") pickFiles([...event.dataTransfer.files]);
  });
}

for (const shortcut of shortcuts) {
  shortcut.addEventListener("click", () => {
    const [input, output] = shortcut.dataset.preset.split(":");
    const inputs = input === "jpg" ? images : [input];
    preset = { key: shortcut.dataset.preset, inputs, output };
    clearFeedback("convert");
    updateFiles();
    if (!files.length || files.some((file) => !inputs.includes(extension(file)))) {
      $("files").accept = inputs.map((ext) => `.${ext}`).join(",");
      $("files").click();
    } else {
      $("target").focus();
    }
  });
}

$("target").addEventListener("change", () => {
  preset = null;
  clearFeedback("convert");
  updateFiles();
});
$("dpi").addEventListener("change", () => clearFeedback("convert"));
$("media-form").addEventListener("input", () => clearFeedback("media"));

function filenameFrom(response) {
  const disposition = response.headers.get("Content-Disposition") || "";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (encoded) return decodeURIComponent(encoded[1]);
  const plain = disposition.match(/filename="?([^";]+)"?/i);
  return plain?.[1] || "filecraft-result";
}

async function run(tool, path, data) {
  if (busy) return;
  busy = true;
  syncSettings();
  const pending = tool === "convert" ? "Converting your file…" : "Fetching media from the platform…";
  showFeedback(tool, pending);
  try {
    const response = await fetch(path, { method: "POST", headers: { "X-Filecraft-Token": token }, body: data });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      showFeedback(tool, detail.error || "Filecraft could not complete this request.", "error");
      return;
    }
    const blob = await response.blob();
    const count = Number(response.headers.get("X-Filecraft-Count") || 1);
    const feedback = showFeedback(tool, count > 1 ? `${count} files are ready in a ZIP.` : "Your file is ready.", "success");
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    resultUrls.set(tool, url);
    link.href = url;
    link.download = filenameFrom(response);
    link.textContent = `Save ${link.download} · ${sizeLabel(blob.size)}`;
    feedback.append(document.createElement("br"), link);
  } catch {
    showFeedback(tool, "The request stopped. Check that Filecraft is still running and try again.", "error");
  } finally {
    busy = false;
    syncSettings();
    if (tool === "media") $("cookies").value = "";
  }
}

$("convert-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (busy || !files.length || !$("target").value) return;
  const data = new FormData();
  files.forEach((file) => data.append("files", file));
  data.set("target", $("target").value);
  data.set("dpi", $("dpi").value);
  run("convert", "/api/convert", data);
});

$("media-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (busy) return;
  run("media", "/api/download", new FormData(event.currentTarget));
});

updateFiles();
