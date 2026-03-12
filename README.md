# Transcript-to-Lecture Generator

Local FastAPI app that turns a YouTube transcript or pasted raw transcript into lecture Markdown, then routes that content through interchangeable renderers such as Markdown download or HTML preview/download.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
cp .env.example .env
uvicorn app:app --reload
```

Open `http://127.0.0.1:8000`.

Set your API key in `.env`:

```dotenv
OPENAI_API_KEY=your_api_key
OPENAI_MODEL=gpt-5-mini
LECTURE_MODEL=gpt-5
PREVIEW_MODEL=gpt-5-mini
LOG_LEVEL=INFO
```

## Prompt files

- `prompts/lecture_prompt.txt`
- `prompts/preview_prompt.txt`

Replace the placeholder prompt text with your own prompts whenever you are ready.

## Output modes

- `markdown`: generate lecture Markdown, render preview locally, download `.md`
- `html`: generate lecture Markdown, then run the HTML renderer strategy, download `.html`

The content stage is independent from the renderer stage, so future approaches can be added without rewriting the rest of the app.

## API

`POST /generate`

```json
{
  "title": "Lecture 1",
  "youtube_url": "https://www.youtube.com/watch?v=...",
  "raw_transcript": "optional pasted transcript",
  "output_format": "markdown"
}
```

Response:

```json
{
  "title": "Lecture 1",
  "lecture_content": "# Structured lecture markdown",
  "lecture_format": "markdown",
  "output_format": "markdown",
  "renderer": "markdown_local_preview",
  "preview_html": "<!DOCTYPE html>...",
  "download_content": "# Structured lecture markdown",
  "download_filename": "lecture-1.md",
  "download_mime_type": "text/markdown; charset=utf-8"
}
```

## Notes

- If both `youtube_url` and `raw_transcript` are provided, the pasted transcript wins.
- If transcript fetching fails, paste the transcript manually and retry.
- The content pipeline always generates Markdown first so renderers can evolve independently.
- Generated lecture files are previewed in the browser and downloaded manually. They are not saved on the server.
- Backend logs print stage-by-stage progress in the terminal, including transcript fetch, model calls, and request timing.
- `LECTURE_MODEL` and `PREVIEW_MODEL` can be set independently. If omitted, both fall back to `OPENAI_MODEL`.
