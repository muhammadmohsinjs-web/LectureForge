from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Literal, Protocol
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

OutputFormat = Literal["markdown"]
LectureFormat = Literal["markdown"]


class TranscriptFetchError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class ConfigurationError(Exception):
    pass


class ModelOutputError(Exception):
    pass


def resolve_static_dir(base_dir: Path) -> Path:
    public_static_dir = base_dir / "public" / "static"
    if public_static_dir.is_dir():
        return public_static_dir
    return base_dir / "static"


def resolve_assets_dir(base_dir: Path) -> Path:
    public_assets_dir = base_dir / "public" / "assets"
    if public_assets_dir.is_dir():
        return public_assets_dir
    return base_dir / "assets"


@dataclass(slots=True)
class AppConfig:
    base_dir: Path
    prompts_dir: Path
    static_dir: Path
    assets_dir: Path
    templates_dir: Path
    lecture_prompt_path: Path
    openai_api_key: str
    openai_model: str
    lecture_model: str
    transcript_proxy_http_url: str
    transcript_proxy_https_url: str
    transcript_webshare_proxy_username: str
    transcript_webshare_proxy_password: str
    transcript_webshare_locations: tuple[str, ...]
    transcript_webshare_retries: int
    transcript_proxy_errors: tuple[str, ...]

    @classmethod
    def from_env(cls, base_dir: Path) -> "AppConfig":
        prompts_dir = base_dir / "prompts"
        openai_model = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip() or "gpt-5-mini"
        transcript_proxy_http_url = os.getenv("TRANSCRIPT_PROXY_HTTP_URL", "").strip()
        transcript_proxy_https_url = os.getenv("TRANSCRIPT_PROXY_HTTPS_URL", "").strip()
        transcript_webshare_proxy_username = os.getenv(
            "TRANSCRIPT_WEBSHARE_PROXY_USERNAME", ""
        ).strip()
        transcript_webshare_proxy_password = os.getenv(
            "TRANSCRIPT_WEBSHARE_PROXY_PASSWORD", ""
        ).strip()
        transcript_webshare_locations = tuple(
            item.strip().upper()
            for item in os.getenv("TRANSCRIPT_WEBSHARE_LOCATIONS", "").split(",")
            if item.strip()
        )
        transcript_webshare_retries = 10
        transcript_proxy_errors: list[str] = []
        transcript_webshare_retries_value = os.getenv("TRANSCRIPT_WEBSHARE_RETRIES", "").strip()
        if transcript_webshare_retries_value:
            try:
                transcript_webshare_retries = int(transcript_webshare_retries_value)
            except ValueError:
                transcript_proxy_errors.append("TRANSCRIPT_WEBSHARE_RETRIES must be an integer.")

        has_generic_proxy = bool(transcript_proxy_http_url or transcript_proxy_https_url)
        has_webshare_proxy = bool(
            transcript_webshare_proxy_username or transcript_webshare_proxy_password
        )
        if has_generic_proxy and has_webshare_proxy:
            transcript_proxy_errors.append(
                "Configure either TRANSCRIPT_PROXY_* or TRANSCRIPT_WEBSHARE_PROXY_* env vars, not both."
            )
        if (
            transcript_webshare_proxy_username
            and not transcript_webshare_proxy_password
        ) or (
            transcript_webshare_proxy_password
            and not transcript_webshare_proxy_username
        ):
            transcript_proxy_errors.append(
                "TRANSCRIPT_WEBSHARE_PROXY_USERNAME and TRANSCRIPT_WEBSHARE_PROXY_PASSWORD must be set together."
            )

        return cls(
            base_dir=base_dir,
            prompts_dir=prompts_dir,
            static_dir=resolve_static_dir(base_dir),
            assets_dir=resolve_assets_dir(base_dir),
            templates_dir=base_dir / "templates",
            lecture_prompt_path=prompts_dir / "lecture_prompt.txt",
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            openai_model=openai_model,
            lecture_model=os.getenv("LECTURE_MODEL", openai_model).strip() or openai_model,
            transcript_proxy_http_url=transcript_proxy_http_url,
            transcript_proxy_https_url=transcript_proxy_https_url,
            transcript_webshare_proxy_username=transcript_webshare_proxy_username,
            transcript_webshare_proxy_password=transcript_webshare_proxy_password,
            transcript_webshare_locations=transcript_webshare_locations,
            transcript_webshare_retries=transcript_webshare_retries,
            transcript_proxy_errors=tuple(transcript_proxy_errors),
        )

    def transcript_proxy_mode(self) -> str:
        if (
            self.transcript_webshare_proxy_username
            and self.transcript_webshare_proxy_password
        ):
            return "webshare"
        if self.transcript_proxy_http_url or self.transcript_proxy_https_url:
            return "generic"
        return "none"


class GenerateRequest(BaseModel):
    title: str | None = None
    youtube_url: str | None = None
    raw_transcript: str | None = None


class GenerateResponse(BaseModel):
    title: str
    lecture_content: str
    lecture_format: LectureFormat = "markdown"
    output_format: OutputFormat
    renderer: str
    preview_html: str
    download_content: str
    download_filename: str
    download_mime_type: str


@dataclass(slots=True)
class RenderedOutput:
    output_format: OutputFormat
    renderer: str
    preview_html: str
    download_content: str
    download_filename: str
    download_mime_type: str


class OutputRenderer(Protocol):
    output_format: OutputFormat
    name: str

    def get_configuration_errors(self, services: "AppServices") -> list[str]:
        ...

    async def render(
        self,
        services: "AppServices",
        title: str,
        lecture_markdown: str,
        youtube_url: str | None,
        request_id: str | None = None,
    ) -> RenderedOutput:
        ...


class MarkdownOutputRenderer:
    output_format: OutputFormat = "markdown"
    name = "markdown_local_preview"

    def get_configuration_errors(self, services: "AppServices") -> list[str]:
        return []

    async def render(
        self,
        services: "AppServices",
        title: str,
        lecture_markdown: str,
        youtube_url: str | None,
        request_id: str | None = None,
    ) -> RenderedOutput:
        log_prefix = format_log_prefix(request_id)
        logger.info("%sRendering local Markdown preview", log_prefix)
        preview_html = render_markdown_preview_html(title, lecture_markdown)
        return RenderedOutput(
            output_format="markdown",
            renderer=self.name,
            preview_html=preview_html,
            download_content=lecture_markdown,
            download_filename=build_download_filename(title, "md"),
            download_mime_type="text/markdown; charset=utf-8",
        )


class AppServices:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.renderers: dict[OutputFormat, OutputRenderer] = {
            "markdown": MarkdownOutputRenderer(),
        }

    def get_renderer(self, output_format: OutputFormat) -> OutputRenderer:
        return self.renderers[output_format]

    def get_prompt_errors(self, prompt_specs: tuple[tuple[str, Path], ...]) -> list[str]:
        errors: list[str] = []
        for label, path in prompt_specs:
            if not path.exists():
                errors.append(f"The {label} file is missing at {path}.")
                continue
            if not path.read_text(encoding="utf-8").strip():
                errors.append(f"The {label} file at {path} is empty.")
        return errors

    def get_configuration_errors(self, output_format: OutputFormat | None = None) -> list[str]:
        errors: list[str] = []
        if not self.config.openai_api_key:
            errors.append("OPENAI_API_KEY is not set.")
        errors.extend(self.config.transcript_proxy_errors)

        errors.extend(
            self.get_prompt_errors((("lecture prompt", self.config.lecture_prompt_path),))
        )

        if output_format is None:
            for renderer in self.renderers.values():
                errors.extend(renderer.get_configuration_errors(self))
        else:
            errors.extend(self.get_renderer(output_format).get_configuration_errors(self))

        return dedupe_errors(errors)

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

        api = self._build_transcript_api_client(request_id=request_id)

        try:
            transcript = api.fetch(video_id)
        except Exception as exc:  # pragma: no cover - third-party error surface
            translated_error = self._translate_transcript_fetch_error(exc)
            logger.warning(
                "%sTranscript fetch failed for video_id=%s: %s",
                log_prefix,
                video_id,
                translated_error,
            )
            logger.debug(
                "%sTranscript fetch stack for video_id=%s",
                log_prefix,
                video_id,
                exc_info=exc,
            )
            raise translated_error from exc

        text = transcript_to_text(transcript)
        if not text:
            logger.warning("%sTranscript fetch returned no text for video_id=%s", log_prefix, video_id)
            raise TranscriptFetchError("No transcript text was returned for this video.")
        logger.info("%sTranscript fetched successfully (%s characters)", log_prefix, len(text))
        return text

    def _build_transcript_api_client(self, request_id: str | None = None) -> Any:
        log_prefix = format_log_prefix(request_id)
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            from youtube_transcript_api.proxies import GenericProxyConfig, WebshareProxyConfig
        except ModuleNotFoundError as exc:
            raise ConfigurationError(
                "youtube-transcript-api is not installed. Install dependencies before fetching transcripts."
            ) from exc

        proxy_config = None
        proxy_mode = self.config.transcript_proxy_mode()
        if proxy_mode == "webshare":
            proxy_config = WebshareProxyConfig(
                proxy_username=self.config.transcript_webshare_proxy_username,
                proxy_password=self.config.transcript_webshare_proxy_password,
                filter_ip_locations=list(self.config.transcript_webshare_locations),
                retries_when_blocked=self.config.transcript_webshare_retries,
            )
        elif proxy_mode == "generic":
            proxy_config = GenericProxyConfig(
                http_url=self.config.transcript_proxy_http_url or None,
                https_url=self.config.transcript_proxy_https_url or None,
            )

        if proxy_config is None:
            logger.info("%sFetching transcript without proxy", log_prefix)
        else:
            logger.info("%sFetching transcript with proxy mode=%s", log_prefix, proxy_mode)
        return YouTubeTranscriptApi(proxy_config=proxy_config)

    def _translate_transcript_fetch_error(self, exc: Exception) -> TranscriptFetchError:
        try:
            from youtube_transcript_api import (
                IpBlocked,
                NoTranscriptFound,
                RequestBlocked,
                TranscriptsDisabled,
                VideoUnavailable,
            )
            from youtube_transcript_api._errors import HTTPError
        except ModuleNotFoundError:
            return TranscriptFetchError("Transcript fetch failed for this video.", status_code=502)
        except ImportError:
            from youtube_transcript_api._errors import (
                HTTPError,
                IpBlocked,
                NoTranscriptFound,
                RequestBlocked,
                TranscriptsDisabled,
                VideoUnavailable,
            )

        if isinstance(exc, (RequestBlocked, IpBlocked)):
            return TranscriptFetchError(
                "YouTube blocked transcript requests from the server IP. Configure transcript proxy env vars for production.",
                status_code=502,
            )
        if isinstance(exc, NoTranscriptFound):
            return TranscriptFetchError("No transcript was found for this video.")
        if isinstance(exc, TranscriptsDisabled):
            return TranscriptFetchError("Transcripts are disabled for this video.")
        if isinstance(exc, VideoUnavailable):
            return TranscriptFetchError("This video is unavailable.")
        if isinstance(exc, HTTPError):
            return TranscriptFetchError(
                "YouTube transcript request failed upstream. Try again or paste the transcript manually.",
                status_code=502,
            )
        return TranscriptFetchError("Transcript fetch failed for this video.", status_code=502)

    async def generate_lecture_markdown(
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
        logger.info("%sStarting content pipeline: transcript to markdown lecture", log_prefix)
        content = await self.run_model(
            prompt,
            payload,
            model=self.config.lecture_model,
            request_id=request_id,
        )

        if looks_like_html_document(content):
            raise ModelOutputError(
                "Stage 1 returned HTML. lecture_prompt.txt must produce Markdown lecture content, not HTML."
            )
        normalized_content = normalize_lecture_markdown(content)
        logger.info(
            "%sFinished content pipeline (%s characters, normalized=%s)",
            log_prefix,
            len(normalized_content),
            "yes" if normalized_content != content.strip() else "no",
        )
        return normalized_content

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
    if config.static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(config.static_dir)), name="static")
    if config.assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(config.assets_dir)), name="assets")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        index_path = config.templates_dir / "index.html"
        return HTMLResponse(index_path.read_text(encoding="utf-8"))

    @app.get("/health")
    async def health() -> dict[str, Any]:
        format_status = {
            output_format: {
                "renderer": services.get_renderer(output_format).name,
                "errors": services.get_configuration_errors(output_format),
            }
            for output_format in services.renderers
        }
        logger.info("Health check requested.")
        return {
            "status": "ok" if all(not item["errors"] for item in format_status.values()) else "degraded",
            "openai_model": config.openai_model,
            "lecture_model": config.lecture_model,
            "transcript_proxy": {
                "configured": config.transcript_proxy_mode() != "none",
                "mode": config.transcript_proxy_mode(),
            },
            "prompt_files": {
                "lecture_prompt": str(config.lecture_prompt_path),
            },
            "output_formats": format_status,
        }

    @app.post("/generate", response_model=GenerateResponse)
    async def generate(request: GenerateRequest) -> GenerateResponse:
        request_id = uuid4().hex[:8]
        started_at = time.perf_counter()
        log_prefix = format_log_prefix(request_id)
        logger.info(
            "%sGeneration request received with markdown output",
            log_prefix,
        )

        config_errors = services.get_configuration_errors("markdown")
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
                    status_code=exc.status_code,
                    detail=f"{exc} Paste the transcript manually to continue.",
                ) from exc
            except ConfigurationError as exc:
                logger.error("%sTranscript configuration error: %s", log_prefix, exc)
                raise HTTPException(status_code=500, detail=str(exc)) from exc

        try:
            lecture_content = await services.generate_lecture_markdown(
                resolved_title,
                transcript,
                youtube_url,
                request_id=request_id,
            )
            lecture_content = normalize_lecture_markdown(lecture_content)
            rendered_output = await services.get_renderer("markdown").render(
                services,
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
        logger.info(
            "%sGeneration completed in %.2fs using renderer=%s",
            log_prefix,
            duration,
            rendered_output.renderer,
        )

        return GenerateResponse(
            title=resolved_title,
            lecture_content=lecture_content,
            lecture_format="markdown",
            output_format=rendered_output.output_format,
            renderer=rendered_output.renderer,
            preview_html=rendered_output.preview_html,
            download_content=rendered_output.download_content,
            download_filename=rendered_output.download_filename,
            download_mime_type=rendered_output.download_mime_type,
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


def normalize_lecture_markdown(raw_markdown: str) -> str:
    text = raw_markdown.strip()
    text = strip_markdown_code_fences(text)
    lines = text.splitlines()
    normalized_lines: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index].rstrip()
        stripped = line.strip()

        if not stripped:
            if normalized_lines and normalized_lines[-1] != "":
                normalized_lines.append("")
            index += 1
            continue

        numbered_match = re.match(r"^\s*(\d+)\.\s+(.+)$", stripped)
        alpha_match = re.match(r"^\s*([A-Z])\.\s+(.+)$", stripped)

        if numbered_match and is_heading_like(numbered_match.group(2)):
            heading_text = numbered_match.group(2).strip()

            if heading_text.lower() == "title":
                next_index = next_non_empty_line_index(lines, index + 1)
                if next_index is not None:
                    candidate_title = lines[next_index].strip()
                    if candidate_title and not starts_markdown_block(candidate_title):
                        normalized_lines.append(f"## {candidate_title}")
                        index = next_index + 1
                        continue

            normalized_lines.append(f"## {heading_text}")
            index += 1
            continue

        if alpha_match and is_heading_like(alpha_match.group(2)):
            normalized_lines.append(f"### {alpha_match.group(2).strip()}")
            index += 1
            continue

        if is_plain_heading(stripped):
            normalized_lines.append(f"## {stripped.rstrip(':')}")
            index += 1
            continue

        normalized_lines.append(line)
        index += 1

    return "\n".join(trim_extra_blank_lines(normalized_lines)).strip()


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


def render_markdown_preview_html(title: str, lecture_markdown: str) -> str:
    body_html = render_markdown_body(lecture_markdown)
    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{escape(title)}</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #f6f0e6;
        --surface: #fffdf8;
        --text: #2d241d;
        --muted: #6c5a4d;
        --border: rgba(64, 48, 29, 0.12);
        --accent: #9d4f2f;
      }}
      * {{
        box-sizing: border-box;
      }}
      body {{
        margin: 0;
        font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", serif;
        color: var(--text);
        background:
          radial-gradient(circle at top left, rgba(211, 156, 94, 0.22), transparent 28%),
          linear-gradient(180deg, #fbf6ee 0%, var(--bg) 100%);
      }}
      main {{
        width: min(960px, calc(100% - 32px));
        margin: 32px auto;
        padding: 32px;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 24px;
        box-shadow: 0 18px 44px rgba(62, 46, 25, 0.1);
      }}
      header {{
        margin-bottom: 28px;
        padding-bottom: 20px;
        border-bottom: 1px solid var(--border);
      }}
      .eyebrow {{
        margin: 0 0 10px;
        font-size: 0.78rem;
        letter-spacing: 0.16em;
        text-transform: uppercase;
        color: var(--accent);
        font-weight: 700;
      }}
      h1, h2, h3, h4 {{
        line-height: 1.1;
        margin: 1.35em 0 0.55em;
      }}
      h1 {{
        margin-top: 0;
        font-size: clamp(2rem, 3vw, 3rem);
      }}
      h2 {{
        font-size: 1.65rem;
      }}
      h3 {{
        font-size: 1.25rem;
      }}
      p, li {{
        font-size: 1.02rem;
        line-height: 1.72;
      }}
      ul, ol {{
        padding-left: 1.35rem;
      }}
      blockquote {{
        margin: 1.4rem 0;
        padding: 0.9rem 1rem;
        border-left: 4px solid rgba(157, 79, 47, 0.38);
        background: rgba(157, 79, 47, 0.06);
      }}
      code {{
        font-family: "SFMono-Regular", "Menlo", monospace;
        font-size: 0.92em;
        background: rgba(45, 36, 29, 0.08);
        padding: 0.12rem 0.35rem;
        border-radius: 0.35rem;
      }}
      pre {{
        overflow-x: auto;
        padding: 1rem;
        border-radius: 16px;
        background: #f5efe5;
      }}
      table {{
        width: 100%;
        border-collapse: collapse;
        margin: 1.2rem 0;
      }}
      th, td {{
        border: 1px solid var(--border);
        padding: 0.75rem;
        text-align: left;
      }}
    </style>
  </head>
  <body>
    <main>
      <header>
        <p class="eyebrow">Markdown Preview</p>
      </header>
      <article>{body_html}</article>
    </main>
  </body>
</html>"""


def render_markdown_body(lecture_markdown: str) -> str:
    try:
        import markdown as markdown_package
    except ModuleNotFoundError:
        return f"<pre>{escape(lecture_markdown)}</pre>"

    return markdown_package.markdown(
        lecture_markdown,
        extensions=["extra", "fenced_code", "tables", "sane_lists", "nl2br"],
    )


def strip_markdown_code_fences(text: str) -> str:
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2:
            return "\n".join(lines[1:-1]).strip()
    return text


def next_non_empty_line_index(lines: list[str], start_index: int) -> int | None:
    for index in range(start_index, len(lines)):
        if lines[index].strip():
            return index
    return None


def starts_markdown_block(line: str) -> bool:
    stripped = line.lstrip()
    return bool(
        re.match(r"^(#{1,6}\s|[-*+]\s|\d+\.\s|[A-Z]\.\s|>|```)", stripped)
    )


def is_heading_like(text: str) -> bool:
    cleaned = text.strip().rstrip(":")
    if not cleaned or len(cleaned) > 90:
        return False
    if cleaned.endswith((".", "!", "?")):
        return False
    return True


def is_plain_heading(text: str) -> bool:
    cleaned = text.strip().rstrip(":")
    if cleaned.startswith("#"):
        return False
    if starts_markdown_block(cleaned):
        return False
    if len(cleaned) > 70 or len(cleaned.split()) > 10:
        return False
    if cleaned.endswith((".", "!", "?")):
        return False

    heading_keywords = {
        "introduction",
        "summary",
        "conclusion",
        "overview",
        "key takeaways",
        "main concepts",
        "main concepts and explanation",
        "important points",
        "final thoughts",
    }
    return cleaned.lower() in heading_keywords


def trim_extra_blank_lines(lines: list[str]) -> list[str]:
    trimmed: list[str] = []
    previous_blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank and previous_blank:
            continue
        trimmed.append(line)
        previous_blank = is_blank
    return trimmed


def build_download_filename(title: str, extension: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    safe_slug = slug or "lecture"
    return f"{safe_slug}.{extension}"


def dedupe_errors(errors: list[str]) -> list[str]:
    return list(dict.fromkeys(errors))


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
