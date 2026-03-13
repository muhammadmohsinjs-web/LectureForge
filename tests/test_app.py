import sys
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app as app_module
from app import AppConfig, TranscriptFetchError, create_app


def make_client() -> TestClient:
    application = create_app()
    application.state.config.openai_api_key = "test-key"
    return TestClient(application, raise_server_exceptions=False)


def test_markdown_output_generates_markdown_download() -> None:
    client = make_client()

    async def fake_generate_lecture_markdown(
        title: str,
        transcript: str,
        youtube_url: str | None,
        request_id: str | None = None,
    ) -> str:
        assert title == "lecture"
        assert "hello world" in transcript
        assert youtube_url is None
        assert request_id is not None
        return "# Clean lecture\n\n- Point one"

    client.app.state.services.generate_lecture_markdown = fake_generate_lecture_markdown

    response = client.post(
        "/generate",
        json={"raw_transcript": "hello world"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["lecture_format"] == "markdown"
    assert data["output_format"] == "markdown"
    assert data["download_filename"].endswith(".md")
    assert data["download_content"].startswith("# Clean lecture")
    assert "<html" in data["preview_html"].lower()


def test_index_page_omits_output_format_field() -> None:
    client = make_client()

    response = client.get("/")

    assert response.status_code == 200
    assert 'id="output_format"' not in response.text
    assert 'href="/assets/classnote-favicon.png"' in response.text
    assert 'src="/assets/logo.svg"' in response.text


def test_outline_style_markdown_is_normalized_into_headings() -> None:
    client = make_client()

    async def fake_generate_lecture_markdown(
        title: str,
        transcript: str,
        youtube_url: str | None,
        request_id: str | None = None,
    ) -> str:
        return "\n".join(
            [
                "1. Title",
                "CS408 Lecture01",
                "",
                "2. Introduction",
                "This is the introduction.",
                "",
                "3. Main Concepts and Explanation",
                "A. Human Computer Interaction",
                "HCI studies people and systems.",
            ]
        )

    client.app.state.services.generate_lecture_markdown = fake_generate_lecture_markdown

    response = client.post(
        "/generate",
        json={"raw_transcript": "hello world"},
    )

    assert response.status_code == 200
    markdown = response.json()["download_content"]
    assert "## CS408 Lecture01" in markdown
    assert "## Introduction" in markdown
    assert "## Main Concepts and Explanation" in markdown
    assert "### Human Computer Interaction" in markdown


def test_generate_ignores_legacy_output_format_field() -> None:
    client = make_client()

    async def fake_generate_lecture_markdown(
        title: str,
        transcript: str,
        youtube_url: str | None,
        request_id: str | None = None,
    ) -> str:
        assert request_id is not None
        return "# Lecture"

    client.app.state.services.generate_lecture_markdown = fake_generate_lecture_markdown

    response = client.post(
        "/generate",
        json={"raw_transcript": "hello world", "output_format": "html"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["output_format"] == "markdown"
    assert data["download_filename"].endswith(".md")
    assert data["download_content"] == "# Lecture"


def test_manual_transcript_overrides_youtube_fetch() -> None:
    client = make_client()

    async def fail_fetch_transcript(_: str, request_id: str | None = None) -> str:
        raise AssertionError("fetch_transcript should not be called when raw_transcript is provided")

    async def fake_fetch_title(_: str, request_id: str | None = None) -> str | None:
        assert request_id is not None
        return "Video title"

    async def fake_generate_lecture_markdown(
        title: str,
        transcript: str,
        youtube_url: str | None,
        request_id: str | None = None,
    ) -> str:
        assert title == "Video title"
        assert transcript == "manual transcript"
        assert youtube_url == "https://www.youtube.com/watch?v=abc123def45"
        assert request_id is not None
        return "# Lecture content"

    client.app.state.services.fetch_transcript = fail_fetch_transcript
    client.app.state.services.fetch_video_title = fake_fetch_title
    client.app.state.services.generate_lecture_markdown = fake_generate_lecture_markdown

    response = client.post(
        "/generate",
        json={
            "youtube_url": "https://www.youtube.com/watch?v=abc123def45",
            "raw_transcript": "manual transcript",
        },
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Video title"


def test_transcript_fetch_failure_returns_manual_fallback_message() -> None:
    client = make_client()

    async def fail_fetch_transcript(_: str, request_id: str | None = None) -> str:
        assert request_id is not None
        raise TranscriptFetchError("Transcript fetch failed for this video.")

    client.app.state.services.fetch_transcript = fail_fetch_transcript

    response = client.post(
        "/generate",
        json={"youtube_url": "https://www.youtube.com/watch?v=abc123def45"},
    )

    assert response.status_code == 400
    assert "Paste the transcript manually to continue" in response.json()["detail"]


def test_transcript_request_blocked_returns_proxy_hint(monkeypatch) -> None:
    import youtube_transcript_api

    client = make_client()

    class FakeYouTubeTranscriptApi:
        def __init__(self, proxy_config=None) -> None:
            self.proxy_config = proxy_config

        def fetch(self, video_id: str):
            raise youtube_transcript_api.RequestBlocked(video_id)

    monkeypatch.setattr(youtube_transcript_api, "YouTubeTranscriptApi", FakeYouTubeTranscriptApi)

    response = client.post(
        "/generate",
        json={"youtube_url": "https://www.youtube.com/watch?v=abc123def45"},
    )

    assert response.status_code == 502
    assert "YouTube blocked transcript requests from the server IP" in response.json()["detail"]
    assert "Paste the transcript manually to continue" in response.json()["detail"]


def test_health_reports_markdown_only_configuration() -> None:
    client = make_client()
    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "preview_model" not in data
    assert "preview_prompt" not in data["prompt_files"]
    assert data["transcript_proxy"] == {"configured": False, "mode": "none"}
    assert list(data["output_formats"]) == ["markdown"]


def test_app_config_prefers_public_static_dir(tmp_path: Path) -> None:
    public_static_dir = tmp_path / "public" / "static"
    public_static_dir.mkdir(parents=True)
    (tmp_path / "static").mkdir()
    public_assets_dir = tmp_path / "public" / "assets"
    public_assets_dir.mkdir(parents=True)
    (tmp_path / "assets").mkdir()

    config = AppConfig.from_env(tmp_path)

    assert config.static_dir == public_static_dir
    assert config.assets_dir == public_assets_dir


def test_app_config_falls_back_to_legacy_static_dir(tmp_path: Path) -> None:
    legacy_static_dir = tmp_path / "static"
    legacy_static_dir.mkdir()
    legacy_assets_dir = tmp_path / "assets"
    legacy_assets_dir.mkdir()

    config = AppConfig.from_env(tmp_path)

    assert config.static_dir == legacy_static_dir
    assert config.assets_dir == legacy_assets_dir


def test_app_config_reads_generic_transcript_proxy(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRANSCRIPT_PROXY_HTTP_URL", "http://proxy.internal:8080")
    monkeypatch.delenv("TRANSCRIPT_PROXY_HTTPS_URL", raising=False)
    monkeypatch.delenv("TRANSCRIPT_WEBSHARE_PROXY_USERNAME", raising=False)
    monkeypatch.delenv("TRANSCRIPT_WEBSHARE_PROXY_PASSWORD", raising=False)

    config = AppConfig.from_env(tmp_path)

    assert config.transcript_proxy_mode() == "generic"
    assert config.transcript_proxy_http_url == "http://proxy.internal:8080"
    assert config.transcript_proxy_errors == ()


def test_fetch_transcript_uses_generic_proxy(monkeypatch, tmp_path: Path) -> None:
    import youtube_transcript_api
    from youtube_transcript_api import proxies

    monkeypatch.setenv("TRANSCRIPT_PROXY_HTTP_URL", "http://proxy.internal:8080")
    monkeypatch.delenv("TRANSCRIPT_PROXY_HTTPS_URL", raising=False)
    monkeypatch.delenv("TRANSCRIPT_WEBSHARE_PROXY_USERNAME", raising=False)
    monkeypatch.delenv("TRANSCRIPT_WEBSHARE_PROXY_PASSWORD", raising=False)

    config = AppConfig.from_env(tmp_path)
    services = app_module.AppServices(config)
    seen: dict[str, object] = {}

    class FakeGenericProxyConfig:
        def __init__(self, http_url=None, https_url=None) -> None:
            seen["http_url"] = http_url
            seen["https_url"] = https_url

    class FakeYouTubeTranscriptApi:
        def __init__(self, proxy_config=None) -> None:
            seen["proxy_config"] = proxy_config

        def fetch(self, video_id: str):
            return [{"text": "hello world"}]

    monkeypatch.setattr(proxies, "GenericProxyConfig", FakeGenericProxyConfig)
    monkeypatch.setattr(youtube_transcript_api, "YouTubeTranscriptApi", FakeYouTubeTranscriptApi)

    transcript = services._fetch_transcript_sync("https://www.youtube.com/watch?v=abc123def45")

    assert transcript == "hello world"
    assert seen["http_url"] == "http://proxy.internal:8080"
    assert seen["https_url"] is None


def test_create_app_skips_static_mount_when_directory_is_missing(monkeypatch) -> None:
    missing_static_dir = Path("/tmp/static-dir-that-does-not-exist")
    monkeypatch.setattr(app_module, "resolve_static_dir", lambda _: missing_static_dir)
    monkeypatch.setattr(app_module, "resolve_assets_dir", lambda _: missing_static_dir)

    application = app_module.create_app()

    assert all(getattr(route, "name", None) != "static" for route in application.routes)
    assert all(getattr(route, "name", None) != "assets" for route in application.routes)
