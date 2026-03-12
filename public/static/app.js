const form = document.getElementById("generator-form");
const generateButton = document.getElementById("generate-button");
const regenerateButton = document.getElementById("regenerate-button");
const downloadButton = document.getElementById("download-button");
const copyMarkdownButton = document.getElementById("copy-markdown-button");
const previewReaderTab = document.getElementById("preview-reader-tab");
const previewMarkdownTab = document.getElementById("preview-markdown-tab");
const statusBox = document.getElementById("status");
const errorBox = document.getElementById("error");
const previewSurface = document.getElementById("preview-surface");
const previewOverlay = document.getElementById("preview-overlay");
const previewTitle = document.getElementById("preview-title");
const previewContext = document.getElementById("preview-context");
const previewMode = document.getElementById("preview-mode");
const previewStateChip = document.getElementById("preview-state-chip");
const loadingTitle = document.getElementById("loading-title");
const loadingStage = document.getElementById("loading-stage");
const loadingProgressBar = document.getElementById("loading-progress-bar");
const loadingStepItems = [...document.querySelectorAll(".loading-step")];
const transcriptWordCount = document.getElementById("transcript-word-count");
const transcriptCharCount = document.getElementById("transcript-char-count");
const transcriptHelper = document.getElementById("transcript-helper");
const sourcePriorityNote = document.getElementById("source-priority-note");
const sourceCards = {
  youtube: document.querySelector('[data-source-card="youtube"]'),
  transcript: document.querySelector('[data-source-card="transcript"]'),
};
const previewRoot = previewSurface.attachShadow({ mode: "open" });

let latestDownloadContent = "";
let latestDownloadFilename = "lecture.md";
let latestDownloadMime = "text/markdown;charset=utf-8";
let latestLectureMarkdown = "";
let latestPreviewHtml = "";
let hasRenderedPreview = false;
let currentPreviewTab = "reader";
let loadingStageTimer = null;
let copyResetTimer = null;

const loadingStages = [
  {
    copy: "Cleaning the transcript and resolving the lecture context.",
    progress: 22,
  },
  {
    copy: "Shaping sections, hierarchy, and a clearer reading flow.",
    progress: 61,
  },
  {
    copy: "Rendering the preview and preparing the export file.",
    progress: 92,
  },
];

const previewModeNote = "Markdown export selected. Reader and raw markdown views stay available.";

function setLoading(isLoading) {
  generateButton.disabled = isLoading;
  regenerateButton.disabled = isLoading;
  downloadButton.disabled = isLoading;
  copyMarkdownButton.disabled = isLoading;
  previewReaderTab.disabled = isLoading;
  previewMarkdownTab.disabled = isLoading || !latestLectureMarkdown;
  generateButton.textContent = isLoading ? "Generating..." : "Generate lecture";
}

function showStatus(message) {
  statusBox.textContent = message;
}

function showError(message) {
  errorBox.hidden = !message;
  errorBox.textContent = message || "";
}

function countWords(text) {
  const normalized = text.trim();
  return normalized ? normalized.split(/\s+/).length : 0;
}

function setPreviewStateChip(label) {
  previewStateChip.textContent = label;
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function setPreviewTab(tab) {
  if (tab === "markdown" && !latestLectureMarkdown) {
    return;
  }

  currentPreviewTab = tab;
  previewReaderTab.classList.toggle("is-active", tab === "reader");
  previewReaderTab.setAttribute("aria-selected", String(tab === "reader"));
  previewMarkdownTab.classList.toggle("is-active", tab === "markdown");
  previewMarkdownTab.setAttribute("aria-selected", String(tab === "markdown"));
  copyMarkdownButton.hidden = !latestLectureMarkdown;

  if (hasRenderedPreview) {
    renderCurrentPreview();
  }
}

function updateLoadingStage(index) {
  const stage = loadingStages[index] || loadingStages[0];
  loadingStage.textContent = stage.copy;
  loadingProgressBar.style.width = `${stage.progress}%`;

  loadingStepItems.forEach((item, itemIndex) => {
    if (itemIndex < index) {
      item.dataset.state = "complete";
      item.classList.remove("is-active");
      return;
    }

    if (itemIndex === index) {
      item.dataset.state = "active";
      item.classList.add("is-active");
      return;
    }

    item.dataset.state = "upcoming";
    item.classList.remove("is-active");
  });
}

function updateTranscriptMetrics() {
  const transcript = form.raw_transcript.value;
  transcriptWordCount.textContent = `${countWords(transcript)} words`;
  transcriptCharCount.textContent = `${transcript.length.toLocaleString()} chars`;

  if (transcript.trim()) {
    transcriptHelper.textContent = "Manual transcript is ready and will be used as the primary source.";
  } else if (form.youtube_url.value.trim()) {
    transcriptHelper.textContent = "No transcript pasted yet. The app will try to fetch one from the YouTube link.";
  } else {
    transcriptHelper.textContent = "Multi-speaker and lightly messy transcripts are fine. The structure is cleaned during generation.";
  }
}

function updateSourceState() {
  const hasUrl = Boolean(form.youtube_url.value.trim());
  const hasTranscript = Boolean(form.raw_transcript.value.trim());

  if (!hasUrl && !hasTranscript) {
    sourceCards.youtube.dataset.state = "idle";
    sourceCards.transcript.dataset.state = "recommended";
    sourcePriorityNote.textContent =
      "Paste a transcript for the most controlled result, or use a YouTube link for a faster start.";
    if (!loadingStageTimer && !hasRenderedPreview) {
      setPreviewStateChip("Awaiting input");
    }
    return;
  }

  if (hasUrl && !hasTranscript) {
    sourceCards.youtube.dataset.state = "active";
    sourceCards.transcript.dataset.state = "idle";
    sourcePriorityNote.textContent =
      "YouTube source selected. If transcript fetch fails, paste the transcript manually and try again.";
    if (!loadingStageTimer && !hasRenderedPreview) {
      setPreviewStateChip("Ready to build");
    }
    return;
  }

  if (!hasUrl && hasTranscript) {
    sourceCards.youtube.dataset.state = "idle";
    sourceCards.transcript.dataset.state = "active";
    sourcePriorityNote.textContent =
      "Manual transcript selected. This gives the cleanest control over the generated lecture.";
    if (!loadingStageTimer && !hasRenderedPreview) {
      setPreviewStateChip("Ready to build");
    }
    return;
  }

  sourceCards.youtube.dataset.state = "secondary";
  sourceCards.transcript.dataset.state = "active";
  sourcePriorityNote.textContent =
    "Both sources are filled. The pasted transcript will be used first, while the YouTube link can still help infer the title.";

  if (!loadingStageTimer && !hasRenderedPreview) {
    setPreviewStateChip("Ready to build");
  }
}

function updatePreviewActions() {
  previewReaderTab.disabled = false;
  previewMarkdownTab.disabled = !latestLectureMarkdown;
  copyMarkdownButton.hidden = !latestLectureMarkdown;

  if (!latestLectureMarkdown && currentPreviewTab === "markdown") {
    currentPreviewTab = "reader";
  }

  previewReaderTab.classList.toggle("is-active", currentPreviewTab === "reader");
  previewReaderTab.setAttribute("aria-selected", String(currentPreviewTab === "reader"));
  previewMarkdownTab.classList.toggle("is-active", currentPreviewTab === "markdown");
  previewMarkdownTab.setAttribute("aria-selected", String(currentPreviewTab === "markdown"));
}

function resetDownloadState() {
  latestDownloadContent = "";
  latestDownloadFilename = "lecture.md";
  latestDownloadMime = "text/markdown;charset=utf-8";
  downloadButton.hidden = true;

  if (!hasRenderedPreview) {
    latestLectureMarkdown = "";
    latestPreviewHtml = "";
    updatePreviewActions();
    renderEmptyState();
  }
}

function parsePreviewHtml(sourceHtml) {
  const parser = new DOMParser();
  const parsed = parser.parseFromString(sourceHtml, "text/html");

  parsed.querySelectorAll("script, iframe, object, embed").forEach((node) => node.remove());
  parsed.querySelectorAll("*").forEach((node) => {
    [...node.attributes].forEach((attribute) => {
      const name = attribute.name.toLowerCase();
      const value = attribute.value.trim().toLowerCase();
      if (name.startsWith("on")) {
        node.removeAttribute(attribute.name);
      }
      if ((name === "href" || name === "src") && value.startsWith("javascript:")) {
        node.removeAttribute(attribute.name);
      }
    });
  });

  const headStyles = [...parsed.head.querySelectorAll("style")]
    .map((style) => style.outerHTML)
    .join("\n");

  const headLinks = [...parsed.head.querySelectorAll("link[rel='stylesheet']")]
    .filter((link) => {
      const href = (link.getAttribute("href") || "").trim().toLowerCase();
      return href && !href.startsWith("http:") && !href.startsWith("https:");
    })
    .map((link) => link.outerHTML)
    .join("\n");

  const bodyContent = parsed.body ? parsed.body.innerHTML : parsed.documentElement.innerHTML;
  return { headStyles, headLinks, bodyContent };
}

function getPreviewBaseStyles() {
  return `
    <style>
      @import url("https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&display=swap");

      :host {
        display: block;
        min-height: 100%;
        color-scheme: light;
      }

      * {
        box-sizing: border-box;
      }

      .preview-shell {
        min-height: 100%;
        padding: clamp(18px, 4vw, 30px);
        background: linear-gradient(180deg, #f6f7f5 0%, #f1f3f1 100%);
      }

      .preview-document,
      .markdown-document,
      .loading-document {
        max-width: 880px;
        margin: 0 auto;
        padding: clamp(24px, 5vw, 48px);
        border: 1px solid rgba(26, 34, 48, 0.08);
        border-radius: 28px;
        background: rgba(255, 255, 255, 0.96);
        box-shadow: 0 16px 40px rgba(26, 34, 48, 0.05);
        color: #1a2230;
      }

      .preview-document {
        font: 1.04rem/1.85 "Source Serif 4", Georgia, serif;
      }

      .preview-document > :first-child,
      .markdown-document > :first-child,
      .loading-document > :first-child {
        margin-top: 0;
      }

      .preview-document > :last-child,
      .markdown-document > :last-child,
      .loading-document > :last-child {
        margin-bottom: 0;
      }

      .preview-document h1,
      .preview-document h2,
      .preview-document h3,
      .preview-document h4,
      .markdown-document h3,
      .loading-document h3 {
        margin: 1.7em 0 0.55em;
        color: #18222f;
        font-family: "Instrument Sans", "Segoe UI", sans-serif;
        font-weight: 600;
        letter-spacing: -0.04em;
        line-height: 1.12;
      }

      .preview-document h1,
      .loading-document h3 {
        font-size: clamp(1.95rem, 4vw, 2.7rem);
      }

      .preview-document h2 {
        font-size: clamp(1.35rem, 2.6vw, 1.9rem);
      }

      .preview-document h3 {
        font-size: 1.15rem;
      }

      .preview-document p,
      .preview-document li,
      .loading-copy,
      .markdown-copy {
        color: #445162;
      }

      .preview-document p,
      .preview-document ul,
      .preview-document ol,
      .preview-document blockquote,
      .preview-document pre,
      .preview-document table {
        margin: 0 0 1.1rem;
      }

      .preview-document ul,
      .preview-document ol {
        padding-left: 1.4rem;
      }

      .preview-document a {
        color: #30465e;
      }

      .preview-document strong {
        color: #18222f;
      }

      .preview-document blockquote {
        margin-left: 0;
        padding: 14px 0 14px 18px;
        border-left: 2px solid rgba(77, 100, 127, 0.28);
        color: #526070;
      }

      .preview-document code,
      .markdown-document code {
        padding: 0.18rem 0.4rem;
        border-radius: 6px;
        background: rgba(26, 34, 48, 0.06);
        font-family: "SFMono-Regular", "SF Mono", Consolas, monospace;
        font-size: 0.92em;
      }

      .preview-document pre {
        overflow: auto;
        padding: 18px;
        border: 1px solid rgba(26, 34, 48, 0.08);
        border-radius: 18px;
        background: #1c2532;
        color: #f5f7fa;
      }

      .preview-document pre code {
        padding: 0;
        background: transparent;
        color: inherit;
      }

      .preview-document table {
        width: 100%;
        border-collapse: collapse;
        overflow: hidden;
        border-radius: 16px;
        border: 1px solid rgba(26, 34, 48, 0.08);
      }

      .preview-document th,
      .preview-document td {
        padding: 12px 14px;
        border-bottom: 1px solid rgba(26, 34, 48, 0.08);
        text-align: left;
      }

      .preview-document th {
        background: rgba(243, 245, 246, 0.92);
      }

      .preview-document img {
        max-width: 100%;
        border-radius: 18px;
      }

      .preview-empty,
      .markdown-head,
      .loading-head {
        display: grid;
        gap: 18px;
      }

      .preview-empty-eyebrow,
      .markdown-eyebrow,
      .loading-eyebrow {
        margin: 0;
        color: #4d647f;
        font-size: 0.72rem;
        font-family: "Instrument Sans", "Segoe UI", sans-serif;
        font-weight: 700;
        letter-spacing: 0.16em;
        text-transform: uppercase;
      }

      .preview-empty h3,
      .loading-document h3 {
        margin: 0;
        max-width: 14ch;
        font-family: "Instrument Sans", "Segoe UI", sans-serif;
        font-size: clamp(1.95rem, 4vw, 2.6rem);
        font-weight: 600;
        line-height: 1.02;
        letter-spacing: -0.04em;
      }

      .preview-empty-copy,
      .loading-copy,
      .markdown-copy {
        max-width: 58ch;
        margin: 0;
        font: 0.98rem/1.7 "Instrument Sans", "Segoe UI", sans-serif;
      }

      .preview-empty-steps {
        display: grid;
        margin-top: 6px;
        border-top: 1px solid rgba(26, 34, 48, 0.08);
      }

      .preview-empty-step {
        display: grid;
        grid-template-columns: minmax(140px, 180px) minmax(0, 1fr);
        gap: 18px;
        padding: 16px 0;
        border-bottom: 1px solid rgba(26, 34, 48, 0.08);
        font-family: "Instrument Sans", "Segoe UI", sans-serif;
      }

      .preview-empty-step strong {
        color: #18222f;
        font-size: 0.95rem;
        font-weight: 600;
      }

      .preview-empty-step span {
        color: #617082;
        line-height: 1.65;
      }

      .markdown-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        margin-top: 4px;
      }

      .markdown-chip {
        display: inline-flex;
        align-items: center;
        min-height: 32px;
        padding: 0 12px;
        border-radius: 999px;
        border: 1px solid rgba(26, 34, 48, 0.08);
        background: rgba(243, 245, 246, 0.92);
        color: #495668;
        font: 0.8rem/1 "Instrument Sans", "Segoe UI", sans-serif;
        font-weight: 600;
      }

      .markdown-code {
        margin-top: 24px;
        padding: 24px;
        border: 1px solid rgba(26, 34, 48, 0.08);
        border-radius: 18px;
        background: #f7f8f6;
        color: #1a2230;
        overflow: auto;
        font: 0.95rem/1.75 "SFMono-Regular", "SF Mono", Consolas, monospace;
        white-space: pre-wrap;
        word-break: break-word;
      }

      .loading-progress {
        position: relative;
        height: 6px;
        margin: 20px 0 18px;
        border-radius: 999px;
        background: rgba(77, 100, 127, 0.1);
        overflow: hidden;
      }

      .loading-progress-bar {
        display: block;
        width: 18%;
        height: 100%;
        border-radius: inherit;
        background: #4d647f;
      }

      .loading-steps {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 10px;
        margin-bottom: 28px;
      }

      .loading-step {
        display: flex;
        align-items: center;
        gap: 10px;
        min-height: 50px;
        padding: 10px 12px;
        border-radius: 14px;
        background: rgba(247, 248, 246, 0.88);
        border: 1px solid rgba(26, 34, 48, 0.08);
        color: #617082;
      }

      .loading-step.active {
        color: #30465e;
        border-color: rgba(77, 100, 127, 0.18);
        background: rgba(255, 255, 255, 0.98);
      }

      .loading-step.complete {
        color: #1a2230;
        background: rgba(255, 255, 255, 0.98);
      }

      .loading-step-index {
        display: inline-grid;
        place-items: center;
        width: 26px;
        height: 26px;
        border-radius: 999px;
        background: rgba(77, 100, 127, 0.09);
        font-size: 0.72rem;
        font-family: "Instrument Sans", "Segoe UI", sans-serif;
        font-weight: 700;
      }

      .loading-step-label {
        font: 0.85rem/1.4 "Instrument Sans", "Segoe UI", sans-serif;
        font-weight: 600;
      }

      .skeleton-stack {
        display: grid;
        gap: 12px;
        margin-top: 22px;
      }

      .skeleton-rule,
      .skeleton-kicker {
        position: relative;
        overflow: hidden;
        border-radius: 999px;
        background: rgba(77, 100, 127, 0.1);
      }

      .skeleton-rule::after,
      .skeleton-kicker::after {
        content: "";
        position: absolute;
        inset: 0;
        transform: translateX(-100%);
        background: linear-gradient(
          90deg,
          transparent,
          rgba(255, 255, 255, 0.72),
          transparent
        );
        animation: shimmer 1.6s infinite;
      }

      .skeleton-kicker {
        height: 10px;
        width: 88px;
      }

      .skeleton-title {
        height: 16px;
        width: min(56%, 360px);
      }

      .skeleton-rule {
        height: 12px;
        width: 100%;
      }

      .skeleton-rule.short {
        width: 68%;
      }

      @keyframes shimmer {
        100% {
          transform: translateX(100%);
        }
      }

      @media (max-width: 760px) {
        .preview-shell {
          padding: 14px;
        }

        .preview-document,
        .markdown-document,
        .loading-document {
          padding: 22px;
          border-radius: 24px;
        }

        .preview-empty-step,
        .loading-steps {
          grid-template-columns: 1fr;
        }

        .preview-empty-step {
          gap: 8px;
        }
      }
    </style>
  `;
}

function renderReaderDocument(sourceHtml) {
  const { headStyles, headLinks, bodyContent } = parsePreviewHtml(sourceHtml);
  previewRoot.innerHTML = `
    ${getPreviewBaseStyles()}
    ${headLinks}
    ${headStyles}
    <div class="preview-shell">
      <article class="preview-document">${bodyContent}</article>
    </div>
  `;
}

function renderMarkdownDocument(markdownText) {
  previewRoot.innerHTML = `
    ${getPreviewBaseStyles()}
    <div class="preview-shell">
      <article class="markdown-document">
        <div class="markdown-head">
          <p class="markdown-eyebrow">Markdown Output</p>
          <h3>Raw lecture markdown, ready to copy or export.</h3>
          <p class="markdown-copy">
            This is the exact markdown file that will be downloaded. Use the copy action in the toolbar if you want the full document on your clipboard.
          </p>
          <div class="markdown-meta">
            <span class="markdown-chip">${countWords(markdownText)} words</span>
            <span class="markdown-chip">${markdownText.length.toLocaleString()} chars</span>
          </div>
        </div>
        <pre class="markdown-code"><code>${escapeHtml(markdownText)}</code></pre>
      </article>
    </div>
  `;
}

function renderLoadingDocument(isRegenerate) {
  const stepsMarkup = loadingStepItems
    .map((item) => {
      const label = item.querySelector(".loading-step-label")?.textContent || "";
      const index = item.querySelector(".loading-step-index")?.textContent || "";
      const state = item.dataset.state || "upcoming";
      return `
        <div class="loading-step ${state === "active" ? "active" : ""} ${state === "complete" ? "complete" : ""}">
          <span class="loading-step-index">${index}</span>
          <span class="loading-step-label">${label}</span>
        </div>
      `;
    })
    .join("");

  previewRoot.innerHTML = `
    ${getPreviewBaseStyles()}
    <div class="preview-shell">
      <article class="loading-document">
        <div class="loading-head">
          <p class="loading-eyebrow">Generating</p>
          <h3>${escapeHtml(isRegenerate ? "Refreshing your lecture" : "Building your lecture")}</h3>
          <p class="loading-copy">${escapeHtml(loadingStage.textContent)}</p>
        </div>

        <div class="loading-progress">
          <span class="loading-progress-bar" style="width: ${loadingProgressBar.style.width || "18%"};"></span>
        </div>

        <div class="loading-steps">${stepsMarkup}</div>

        <div class="skeleton-stack">
          <div class="skeleton-kicker"></div>
          <div class="skeleton-rule skeleton-title"></div>
          <div class="skeleton-rule"></div>
          <div class="skeleton-rule short"></div>
        </div>

        <div class="skeleton-stack">
          <div class="skeleton-kicker"></div>
          <div class="skeleton-rule"></div>
          <div class="skeleton-rule"></div>
          <div class="skeleton-rule short"></div>
        </div>

        <div class="skeleton-stack">
          <div class="skeleton-kicker"></div>
          <div class="skeleton-rule"></div>
          <div class="skeleton-rule short"></div>
        </div>
      </article>
    </div>
  `;
}

function renderEmptyState() {
  hasRenderedPreview = false;
  previewTitle.textContent = "Lecture Preview";
  previewContext.textContent = "Your generated lecture will appear here once the source is ready.";
  previewMode.textContent = previewModeNote;
  setPreviewStateChip("Awaiting input");

  previewRoot.innerHTML = `
    ${getPreviewBaseStyles()}
    <div class="preview-shell">
      <article class="preview-document">
        <section class="preview-empty">
          <p class="preview-empty-eyebrow">Ready to build</p>
          <h3>A readable lecture draft will appear here.</h3>
          <p class="preview-empty-copy">
            Choose a source on the left and generate a cleaner version designed for reading, reviewing, and export.
          </p>

          <div class="preview-empty-steps">
            <div class="preview-empty-step">
              <strong>Add transcript</strong>
              <span>Paste raw material directly or provide a lecture URL to fetch the source.</span>
            </div>
            <div class="preview-empty-step">
              <strong>Generate structure</strong>
              <span>The draft is cleaned, organized into sections, and prepared for export.</span>
            </div>
            <div class="preview-empty-step">
              <strong>Review in context</strong>
              <span>Check pacing, hierarchy, and readability before you download the final lecture.</span>
            </div>
          </div>
        </section>
      </article>
    </div>
  `;
}

function renderCurrentPreview() {
  updatePreviewActions();

  if (!hasRenderedPreview) {
    renderEmptyState();
    return;
  }

  if (currentPreviewTab === "markdown") {
    renderMarkdownDocument(latestLectureMarkdown);
    return;
  }

  renderReaderDocument(latestPreviewHtml);
}

function startLoadingPreview(isRegenerate) {
  stopLoadingPreview();
  loadingTitle.textContent = isRegenerate ? "Refreshing your lecture" : "Building your lecture";
  previewOverlay.hidden = true;
  previewContext.textContent = isRegenerate
    ? "Updating the current draft while keeping the reader workspace in focus."
    : "Transforming the source into a study-ready lecture draft.";
  previewMode.textContent = "Markdown output in progress";
  setPreviewStateChip(isRegenerate ? "Refreshing" : "Generating");
  currentPreviewTab = "reader";
  updateLoadingStage(0);
  renderLoadingDocument(isRegenerate);

  let stageIndex = 0;
  loadingStageTimer = window.setInterval(() => {
    if (stageIndex < loadingStages.length - 1) {
      stageIndex += 1;
    }
    updateLoadingStage(stageIndex);
    renderLoadingDocument(isRegenerate);
  }, 1800);
}

function stopLoadingPreview() {
  if (loadingStageTimer) {
    window.clearInterval(loadingStageTimer);
    loadingStageTimer = null;
  }

  previewOverlay.hidden = true;
  delete previewOverlay.dataset.mode;
}

function renderPreview(data) {
  stopLoadingPreview();
  latestDownloadContent = data.download_content;
  latestDownloadFilename = data.download_filename;
  latestDownloadMime = data.download_mime_type;
  latestLectureMarkdown = data.lecture_content || "";
  latestPreviewHtml = data.preview_html;
  hasRenderedPreview = true;

  previewTitle.textContent = data.title || "Generated lecture";
  previewContext.textContent = "Review the composition, spacing, and structure before you download.";
  previewMode.textContent = `Markdown export via ${data.renderer}`;
  setPreviewStateChip("Ready to review");

  downloadButton.textContent = "Download Markdown";
  downloadButton.hidden = false;
  regenerateButton.hidden = false;
  currentPreviewTab = "markdown";
  renderCurrentPreview();
}

async function copyMarkdownToClipboard() {
  if (!latestLectureMarkdown) {
    return;
  }

  try {
    await navigator.clipboard.writeText(latestLectureMarkdown);
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = latestLectureMarkdown;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "absolute";
    textarea.style.left = "-9999px";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }

  copyMarkdownButton.textContent = "Copied";
  window.clearTimeout(copyResetTimer);
  copyResetTimer = window.setTimeout(() => {
    copyMarkdownButton.textContent = "Copy markdown";
  }, 1800);
}

async function generateLecture() {
  showError("");
  const hadPreview = hasRenderedPreview;
  resetDownloadState();
  startLoadingPreview(hadPreview);
  showStatus("Preparing your markdown lecture...");
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

    renderPreview(data);
    showStatus("Lecture generated in markdown. Review the draft or download the file.");
  } catch (error) {
    stopLoadingPreview();
    showError(error.message || "Generation failed.");
    previewContext.textContent = hadPreview
      ? "The last successful draft is still visible while you resolve the issue."
      : "Generation stopped before the first preview was created.";
    previewMode.textContent = "Fix the issue and try again.";
    setPreviewStateChip("Needs attention");
    showStatus("Generation failed.");
    if (hadPreview) {
      hasRenderedPreview = true;
      renderCurrentPreview();
    } else {
      renderEmptyState();
    }
  } finally {
    setLoading(false);
    updateSourceState();
    updatePreviewActions();
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
  if (!latestDownloadContent) {
    return;
  }

  const blob = new Blob([latestDownloadContent], { type: latestDownloadMime });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = latestDownloadFilename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
});

copyMarkdownButton.addEventListener("click", async () => {
  await copyMarkdownToClipboard();
});

previewReaderTab.addEventListener("click", () => {
  setPreviewTab("reader");
});

previewMarkdownTab.addEventListener("click", () => {
  setPreviewTab("markdown");
});

form.youtube_url.addEventListener("input", () => {
  updateSourceState();
  updateTranscriptMetrics();
});
form.raw_transcript.addEventListener("input", () => {
  updateTranscriptMetrics();
  updateSourceState();
});

document.querySelectorAll("[data-focus-target]").forEach((card) => {
  card.addEventListener("click", () => {
    const target = document.getElementById(card.dataset.focusTarget);
    target?.focus();
  });
});

updateTranscriptMetrics();
updateSourceState();
updatePreviewActions();
resetDownloadState();
