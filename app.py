from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional until dependencies are installed
    load_dotenv = None

logger = logging.getLogger("transcript_to_lecture")


class TranscriptFetchError(Exception):
    pass


class ConfigurationError(Exception):
    pass


class ModelOutputError(Exception):
    pass


@dataclass(slots=True)
class AppConfig:
    base_dir: Path
    prompts_dir: Path
    static_dir: Path
    templates_dir: Path
    lecture_prompt_path: Path
    preview_prompt_path: Path
    openai_api_key: str
    openai_model: str
    lecture_model: str
    preview_model: str

    @classmethod
    def from_env(cls, base_dir: Path) -> "AppConfig":
        prompts_dir = base_dir / "prompts"
        openai_model = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip() or "gpt-5-mini"
        return cls(
            base_dir=base_dir,
            prompts_dir=prompts_dir,
            static_dir=base_dir / "static",
            templates_dir=base_dir / "templates",
            lecture_prompt_path=prompts_dir / "lecture_prompt.txt",
            preview_prompt_path=prompts_dir / "preview_prompt.txt",
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            openai_model=openai_model,
            lecture_model=os.getenv("LECTURE_MODEL", openai_model).strip() or openai_model,
            preview_model=os.getenv("PREVIEW_MODEL", openai_model).strip() or openai_model,
        )


class GenerateRequest(BaseModel):
    title: str | None = None
    youtube_url: str | None = None
    raw_transcript: str | None = None


class GenerateResponse(BaseModel):
    title: str
    lecture_content: str
    preview_html: str


class AppServices:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def get_configuration_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.config.openai_api_key:
            errors.append("OPENAI_API_KEY is not set.")

        for label, path in (
            ("lecture prompt", self.config.lecture_prompt_path),
            ("preview prompt", self.config.preview_prompt_path),
        ):
            if not path.exists():
                errors.append(f"The {label} file is missing at {path}.")
                continue
            if not path.read_text(encoding="utf-8").strip():
                errors.append(f"The {label} file at {path} is empty.")

        return errors

    def load_prompt(self, path: Path, label: str) -> str:
        if not path.exists():
            raise ConfigurationError(f"{label} is missing at {path}.")

        content = path.read_text(encoding="utf-8").strip()
        if not content:
            raise ConfigurationError(f"{label} at {path} is empty.")
        return content

    async def fetch_video_title(self, youtube_url: str, request_id: str | None = None) -> str | None:
        return await run_in_threadpool(self._fetch_video_title_sync, youtube_url, request_id)

    def _fetch_video_title_sync(self, youtube_url: str, request_id: str | None = None) -> str | None:
        log_prefix = format_log_prefix(request_id)
        logger.info("%sFetching video title from YouTube oEmbed", log_prefix)
        oembed_url = (
            "https://www.youtube.com/oembed"
            f"?url={quote(youtube_url, safe='')}&format=json"
        )
        request = Request(
            oembed_url,
            headers={"User-Agent": "TranscriptToLecture/1.0"},
        )

        try:
            with urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, ValueError):
            logger.warning("%sCould not resolve video title from URL", log_prefix)
            return None

        title = str(data.get("title", "")).strip()
        if title:
            logger.info("%sResolved video title: %s", log_prefix, title)
        return title or None

    async def fetch_transcript(self, youtube_url: str, request_id: str | None = None) -> str:
        return await run_in_threadpool(self._fetch_transcript_sync, youtube_url, request_id)

    def _fetch_transcript_sync(self, youtube_url: str, request_id: str | None = None) -> str:
        log_prefix = format_log_prefix(request_id)
        video_id = extract_youtube_video_id(youtube_url)
        if not video_id:
            logger.warning("%sCould not extract video ID from URL: %s", log_prefix, youtube_url)
            raise TranscriptFetchError("The YouTube URL is invalid or the video ID could not be parsed.")
        logger.info("%sFetching transcript for video_id=%s", log_prefix, video_id)

        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except ModuleNotFoundError as exc:
            raise ConfigurationError(
                "youtube-transcript-api is not installed. Install dependencies before fetching transcripts."
            ) from exc

        try:
            transcript = YouTubeTranscriptApi().fetch(video_id)
        except Exception as exc:  # pragma: no cover - third-party error surface
            logger.exception("%sTranscript fetch failed for video_id=%s", log_prefix, video_id)
            raise TranscriptFetchError(
                "Transcript fetch failed for this video."
            ) from exc

        text = transcript_to_text(transcript)
        if not text:
            logger.warning("%sTranscript fetch returned no text for video_id=%s", log_prefix, video_id)
            raise TranscriptFetchError("No transcript text was returned for this video.")
        logger.info("%sTranscript fetched successfully (%s characters)", log_prefix, len(text))
        return text

    async def generate_lecture_content(
        self,
        title: str,
        transcript: str,
        youtube_url: str | None,
        request_id: str | None = None,
    ) -> str:
        log_prefix = format_log_prefix(request_id)
        prompt = self.load_prompt(self.config.lecture_prompt_path, "lecture_prompt.txt")
        payload = json.dumps(
            {
                "lecture_title": title,
                "youtube_url": youtube_url,
                "raw_transcript": transcript,
            },
            ensure_ascii=False,
            indent=2,
        )
        logger.info("%sStarting stage 1: transcript to lecture content", log_prefix)
        content = await self.run_model(
            prompt,
            payload,
            model=self.config.lecture_model,
            request_id=request_id,
        )

        if looks_like_html_document(content):
            raise ModelOutputError(
                "Stage 1 returned HTML. lecture_prompt.txt must produce structured lecture content, not HTML."
            )
        logger.info("%sFinished stage 1 (%s characters)", log_prefix, len(content.strip()))
        return content.strip()

    async def generate_preview_html(
        self,
        title: str,
        lecture_content: str,
        youtube_url: str | None,
        request_id: str | None = None,
    ) -> str:
        log_prefix = format_log_prefix(request_id)
        prompt = self.load_prompt(self.config.preview_prompt_path, "preview_prompt.txt")
        payload = json.dumps(
            {
                "lecture_title": title,
                "youtube_url": youtube_url,
                "lecture_content": lecture_content,
            },
            ensure_ascii=False,
            indent=2,
        )
        logger.info("%sStarting stage 2: lecture content to HTML preview", log_prefix)
        response_text = await self.run_model(
            prompt,
            payload,
            model=self.config.preview_model,
            request_id=request_id,
        )
        preview_html = extract_html_document(response_text)
        if not preview_html:
            raise ModelOutputError(
                "Stage 2 did not return a complete HTML document."
            )
        logger.info("%sFinished stage 2 (%s characters)", log_prefix, len(preview_html))
        return preview_html

    async def run_model(
        self,
        instructions: str,
        user_input: str,
        model: str,
        request_id: str | None = None,
    ) -> str:
        return await run_in_threadpool(self._run_model_sync, instructions, user_input, model, request_id)

    def _run_model_sync(
        self,
        instructions: str,
        user_input: str,
        model: str,
        request_id: str | None = None,
    ) -> str:
        log_prefix = format_log_prefix(request_id)
        if not self.config.openai_api_key:
            raise ConfigurationError("OPENAI_API_KEY is not set.")

        try:
            from openai import OpenAI
        except ModuleNotFoundError as exc:
            raise ConfigurationError(
                "The openai package is not installed. Install dependencies before generating lectures."
            ) from exc

        client = OpenAI(api_key=self.config.openai_api_key)
        logger.info(
            "%sCalling OpenAI Responses API with model=%s (input=%s chars)",
            log_prefix,
            model,
            len(user_input),
        )
        response = client.responses.create(
            model=model,
            instructions=instructions,
            input=user_input,
        )
        output_text = extract_response_text(response)
        if not output_text:
            raise ModelOutputError("The model returned an empty response.")
        logger.info("%sModel call completed (%s characters)", log_prefix, len(output_text.strip()))
        return output_text.strip()


def create_app() -> FastAPI:
    base_dir = Path(__file__).resolve().parent
    if load_dotenv is not None:
        load_dotenv(base_dir / ".env")
    config = AppConfig.from_env(base_dir)
    configure_logging()
    services = AppServices(config)

    app = FastAPI(title="Transcript to Lecture Generator")
    app.state.config = config
    app.state.services = services
    app.mount("/static", StaticFiles(directory=str(config.static_dir)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        index_path = config.templates_dir / "index.html"
        return HTMLResponse(index_path.read_text(encoding="utf-8"))

    @app.get("/health")
    async def health() -> dict[str, Any]:
        errors = services.get_configuration_errors()
        logger.info("Health check requested. status=%s", "ok" if not errors else "degraded")
        return {
            "status": "ok" if not errors else "degraded",
            "openai_model": config.openai_model,
            "lecture_model": config.lecture_model,
            "preview_model": config.preview_model,
            "prompt_files": {
                "lecture_prompt": str(config.lecture_prompt_path),
                "preview_prompt": str(config.preview_prompt_path),
            },
            "errors": errors,
        }

    @app.post("/generate", response_model=GenerateResponse)
    async def generate(request: GenerateRequest) -> GenerateResponse:
        request_id = uuid4().hex[:8]
        started_at = time.perf_counter()
        log_prefix = format_log_prefix(request_id)
        logger.info("%sGeneration request received", log_prefix)

        config_errors = services.get_configuration_errors()
        if config_errors:
            logger.error("%sConfiguration error: %s", log_prefix, " ".join(config_errors))
            raise HTTPException(
                status_code=500,
                detail="Configuration error: " + " ".join(config_errors),
            )

        title = clean_text(request.title)
        youtube_url = clean_text(request.youtube_url)
        raw_transcript = clean_text(request.raw_transcript)

        if not raw_transcript and not youtube_url:
            logger.warning("%sRequest rejected: no YouTube URL or transcript provided", log_prefix)
            raise HTTPException(
                status_code=422,
                detail="Provide either a YouTube URL or a raw transcript.",
            )

        if not title and youtube_url:
            title = await services.fetch_video_title(youtube_url, request_id=request_id)
        resolved_title = title or "lecture"
        logger.info("%sResolved title: %s", log_prefix, resolved_title)

        if raw_transcript:
            transcript = normalize_transcript(raw_transcript)
            logger.info("%sUsing manually provided transcript (%s characters)", log_prefix, len(transcript))
        else:
            try:
                transcript = normalize_transcript(
                    await services.fetch_transcript(youtube_url or "", request_id=request_id)
                )
            except TranscriptFetchError as exc:
                logger.warning("%sTranscript fetch error: %s", log_prefix, exc)
                raise HTTPException(
                    status_code=400,
                    detail=f"{exc} Paste the transcript manually to continue.",
                ) from exc
            except ConfigurationError as exc:
                logger.error("%sTranscript configuration error: %s", log_prefix, exc)
                raise HTTPException(status_code=500, detail=str(exc)) from exc

        try:
            lecture_content = await services.generate_lecture_content(
                resolved_title,
                transcript,
                youtube_url,
                request_id=request_id,
            )
            preview_html = await services.generate_preview_html(
                resolved_title,
                lecture_content,
                youtube_url,
                request_id=request_id,
            )
        except ConfigurationError as exc:
            logger.error("%sGeneration configuration error: %s", log_prefix, exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except ModelOutputError as exc:
            logger.error("%sModel output error: %s", log_prefix, exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception:
            logger.exception("%sUnexpected error during generation", log_prefix)
            raise

        duration = time.perf_counter() - started_at
        logger.info("%sGeneration completed in %.2fs", log_prefix, duration)

        return GenerateResponse(
            title=resolved_title,
            lecture_content=lecture_content,
            preview_html=preview_html,
        )

    return app


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def normalize_transcript(raw_transcript: str) -> str:
    lines = [line.strip() for line in raw_transcript.splitlines() if line.strip()]
    return "\n".join(lines)


def extract_youtube_video_id(url: str) -> str | None:
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    path = parsed.path.strip("/")

    if host in {"youtu.be", "www.youtu.be"} and path:
        return path.split("/")[0]

    if host.endswith("youtube.com"):
        if path == "watch":
            return parse_qs(parsed.query).get("v", [None])[0]
        if path.startswith("embed/") or path.startswith("shorts/"):
            return path.split("/", 1)[1].split("/")[0]

    return None


def transcript_to_text(transcript: Any) -> str:
    snippets: list[str] = []
    for snippet in transcript:
        text = getattr(snippet, "text", None)
        if text is None and isinstance(snippet, dict):
            text = snippet.get("text")
        if text:
            snippets.append(str(text).strip())
    return "\n".join(part for part in snippets if part)


def looks_like_html_document(content: str) -> bool:
    normalized = content.strip().lower()
    return "<html" in normalized and "</html>" in normalized


def extract_html_document(content: str) -> str | None:
    if looks_like_html_document(content):
        return content.strip()

    match = re.search(
        r"```(?:html)?\s*(<!doctype html.*?</html>|<html.*?</html>)\s*```",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    return None


def extract_response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text)

    collected: list[str] = []
    for item in getattr(response, "output", []) or []:
        contents = getattr(item, "content", None)
        if contents is None and isinstance(item, dict):
            contents = item.get("content", [])

        for content in contents or []:
            content_type = getattr(content, "type", None)
            if content_type is None and isinstance(content, dict):
                content_type = content.get("type")

            if content_type != "output_text":
                continue

            text = getattr(content, "text", None)
            if text is None and isinstance(content, dict):
                text = content.get("text")
            if text:
                collected.append(str(text))

    return "".join(collected).strip()


def configure_logging() -> None:
    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        )
    else:
        logging.getLogger().setLevel(log_level)

    logger.setLevel(log_level)


def format_log_prefix(request_id: str | None) -> str:
    return f"[request:{request_id}] " if request_id else ""


app = create_app()
