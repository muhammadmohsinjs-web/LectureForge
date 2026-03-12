const form = document.getElementById("generator-form");
const generateButton = document.getElementById("generate-button");
const regenerateButton = document.getElementById("regenerate-button");
const downloadButton = document.getElementById("download-button");
const statusBox = document.getElementById("status");
const errorBox = document.getElementById("error");
const previewFrame = document.getElementById("preview-frame");
const previewTitle = document.getElementById("preview-title");

let latestHtml = "";
let latestTitle = "lecture";

function setLoading(isLoading) {
  generateButton.disabled = isLoading;
  regenerateButton.disabled = isLoading;
  downloadButton.disabled = isLoading;
  generateButton.textContent = isLoading ? "Generating..." : "Generate lecture";
}

function showStatus(message) {
  statusBox.textContent = message;
}

function showError(message) {
  errorBox.hidden = !message;
  errorBox.textContent = message || "";
}

function toFilename(title) {
  const base = (title || "lecture")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `${base || "lecture"}.html`;
}

function renderPreview(title, html) {
  latestTitle = title || "lecture";
  latestHtml = html;
  previewTitle.textContent = latestTitle;
  previewFrame.srcdoc = html;
  downloadButton.hidden = false;
  regenerateButton.hidden = false;
}

async function generateLecture() {
  showError("");
  showStatus("Preparing your lecture...");
  setLoading(true);

  const payload = {
    title: form.title.value.trim() || null,
    youtube_url: form.youtube_url.value.trim() || null,
    raw_transcript: form.raw_transcript.value.trim() || null,
  };

  try {
    const response = await fetch("/generate", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "Generation failed.");
    }

    renderPreview(data.title, data.preview_html);
    showStatus("Lecture generated. Review the preview or download the HTML.");
  } catch (error) {
    showError(error.message || "Generation failed.");
    showStatus("Generation failed.");
  } finally {
    setLoading(false);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  await generateLecture();
});

regenerateButton.addEventListener("click", async () => {
  await generateLecture();
});

downloadButton.addEventListener("click", () => {
  if (!latestHtml) {
    return;
  }

  const blob = new Blob([latestHtml], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = toFilename(latestTitle);
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
});
