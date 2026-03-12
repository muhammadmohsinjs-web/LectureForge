# Transcript-to-Lecture Generator

Local FastAPI app that turns a YouTube transcript or pasted raw transcript into lecture Markdown, then renders a local reading preview and Markdown download.

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
LOG_LEVEL=INFO
```

## Prompt files

- `prompts/lecture_prompt.txt`

Replace the placeholder prompt text with your own prompt whenever you are ready.

## Output modes

- `markdown`: generate lecture Markdown, render preview locally, download `.md`

## API

`POST /generate`

```json
{
  "title": "Lecture 1",
  "youtube_url": "https://www.youtube.com/watch?v=...",
  "raw_transcript": "optional pasted transcript"
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
- The app only generates Markdown output.
- Generated lecture files are previewed in the browser and downloaded manually. They are not saved on the server.
- Backend logs print stage-by-stage progress in the terminal, including transcript fetch, model calls, and request timing.
- `LECTURE_MODEL` can be set independently. If omitted, it falls back to `OPENAI_MODEL`.

## Deploy on Vercel

This repo is configured for Vercel's Python runtime with `app.py` as the ASGI entrypoint and static assets served from `public/static`.

1. Push the repository to GitHub.
2. Import the repository into Vercel.
3. Add these environment variables in the Vercel project settings:
   - `OPENAI_API_KEY`
   - `OPENAI_MODEL`
   - `LECTURE_MODEL`
   - `LOG_LEVEL`
4. Deploy the project.

After the first deploy, validate:

- `GET /health`
- `GET /`
- `POST /generate` with a pasted transcript
- `POST /generate` with a YouTube URL

If generation runs longer than expected on your Vercel plan, move the entrypoint under `api/` and then tune the function settings there.
