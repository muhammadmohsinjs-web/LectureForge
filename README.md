# Transcript-to-Lecture Generator

Local FastAPI app that turns a YouTube transcript or pasted raw transcript into a downloadable HTML lecture using a two-step prompt pipeline.

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
  "lecture_content": "Structured lecture content",
  "preview_html": "<!DOCTYPE html>..."
}
```

## Notes

- If both `youtube_url` and `raw_transcript` are provided, the pasted transcript wins.
- If transcript fetching fails, paste the transcript manually and retry.
- Generated lecture HTML is previewed in the browser and downloaded manually. It is not saved on the server.
- Backend logs print stage-by-stage progress in the terminal, including transcript fetch, model calls, and request timing.
- `LECTURE_MODEL` and `PREVIEW_MODEL` can be set independently. If omitted, both fall back to `OPENAI_MODEL`.
