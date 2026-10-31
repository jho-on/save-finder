const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file");
const fileName = document.getElementById("file-name");
const submit = document.getElementById("submit");
const feedback = document.getElementById("feedback");

let selectedFile = null;

function showError(message) {
  feedback.className = "feedback is-visible is-error";
  feedback.textContent = message;
}

function clearFeedback() {
  feedback.className = "feedback";
  feedback.textContent = "";
}

function selectFile(file) {
  clearFeedback();
  if (!file) {
    return;
  }
  if (!file.name.toLowerCase().endsWith(".zip")) {
    selectedFile = null;
    submit.disabled = true;
    fileName.textContent = "";
    showError("Escolha o arquivo ZIP da exportação, sem descompactar.");
    return;
  }
  selectedFile = file;
  fileName.textContent = `${file.name} · ${formatBytes(file.size)}`;
  submit.disabled = false;
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function setBusy(busy) {
  submit.disabled = busy || !selectedFile;
  submit.textContent = busy ? "Processando…" : "Importar";
  dropzone.setAttribute("aria-busy", String(busy));
}

function showResult(data) {
  const labels = {
    new: "Novos",
    existing: "Já existentes",
    ignored: "Ignorados",
    unreadable: "Arquivos ilegíveis",
  };
  feedback.className = "feedback is-visible is-success";
  feedback.replaceChildren();

  const title = document.createElement("h2");
  title.textContent = `Importação concluída: ${data.total} item(ns) no arquivo.`;
  feedback.append(title);

  const list = document.createElement("ul");
  for (const key of ["new", "existing", "ignored", "unreadable"]) {
    if (data[key] === undefined) continue;
    const item = document.createElement("li");
    item.textContent = `${labels[key]}: ${data[key]}`;
    list.append(item);
  }
  feedback.append(list);
}

async function upload() {
  if (!selectedFile) {
    return;
  }
  clearFeedback();
  setBusy(true);

  const body = new FormData();
  body.append("file", selectedFile, selectedFile.name);

  try {
    const response = await fetch("/api/import", { method: "POST", body });
    let data = {};
    try {
      data = await response.json();
    } catch {
      // Keep the generic message below when the server returns no JSON.
    }
    if (!response.ok) {
      showError(data.detail || "Não foi possível importar o arquivo.");
      return;
    }
    showResult(data);
    selectedFile = null;
    fileInput.value = "";
    fileName.textContent = "";
  } catch {
    showError("Falha de rede ao enviar o arquivo. O servidor local está rodando?");
  } finally {
    setBusy(false);
  }
}

dropzone.addEventListener("click", () => fileInput.click());

dropzone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    fileInput.click();
  }
});

fileInput.addEventListener("change", () => selectFile(fileInput.files[0]));
submit.addEventListener("click", upload);

for (const eventName of ["dragenter", "dragover"]) {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.add("is-dragging");
  });
}

for (const eventName of ["dragleave", "dragend"]) {
  dropzone.addEventListener(eventName, () => {
    dropzone.classList.remove("is-dragging");
  });
}

dropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropzone.classList.remove("is-dragging");
  selectFile(event.dataTransfer.files[0]);
});
