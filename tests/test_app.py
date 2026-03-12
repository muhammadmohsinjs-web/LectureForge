import sys
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import ModelOutputError, TranscriptFetchError, create_app


def make_client() -> TestClient:
    application = create_app()
    application.state.config.openai_api_key = "test-key"
    return TestClient(application, raise_server_exceptions=False)


def test_manual_transcript_generates_preview() -> None:
    client = make_client()

    async def fake_generate_lecture_content(
        title: str,
        transcript: str,
        youtube_url: str | None,
        request_id: str | None = None,
    ) -> str:
        assert title == "lecture"
        assert "hello world" in transcript
        assert youtube_url is None
        assert request_id is not None
        return "# Clean lecture"

    async def fake_generate_preview_html(
        title: str,
        lecture_content: str,
        youtube_url: str | None,
        request_id: str | None = None,
    ) -> str:
        assert title == "lecture"
        assert lecture_content == "# Clean lecture"
        assert youtube_url is None
        assert request_id is not None
        return "<!DOCTYPE html><html><body><h1>Lecture</h1></body></html>"

    client.app.state.services.generate_lecture_content = fake_generate_lecture_content
    client.app.state.services.generate_preview_html = fake_generate_preview_html

    response = client.post(
        "/generate",
        json={"raw_transcript": "hello world"},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "lecture"
    assert "<html>" in response.json()["preview_html"]


def test_manual_transcript_overrides_youtube_fetch() -> None:
    client = make_client()

    async def fail_fetch_transcript(_: str, request_id: str | None = None) -> str:
        raise AssertionError("fetch_transcript should not be called when raw_transcript is provided")

    async def fake_fetch_title(_: str, request_id: str | None = None) -> str | None:
        assert request_id is not None
        return "Video title"

    async def fake_generate_lecture_content(
        title: str,
        transcript: str,
        youtube_url: str | None,
        request_id: str | None = None,
    ) -> str:
        assert title == "Video title"
        assert transcript == "manual transcript"
        assert youtube_url == "https://www.youtube.com/watch?v=abc123def45"
        assert request_id is not None
        return "Lecture content"

    async def fake_generate_preview_html(
        title: str,
        lecture_content: str,
        youtube_url: str | None,
        request_id: str | None = None,
    ) -> str:
        assert request_id is not None
        return "<!DOCTYPE html><html><body>Preview</body></html>"

    client.app.state.services.fetch_transcript = fail_fetch_transcript
    client.app.state.services.fetch_video_title = fake_fetch_title
    client.app.state.services.generate_lecture_content = fake_generate_lecture_content
    client.app.state.services.generate_preview_html = fake_generate_preview_html

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


def test_missing_prompt_file_returns_configuration_error() -> None:
    client = make_client()
    client.app.state.config.lecture_prompt_path = Path("/tmp/does-not-exist.txt")

    response = client.post(
        "/generate",
        json={"raw_transcript": "content"},
    )

    assert response.status_code == 500
    assert "Configuration error" in response.json()["detail"]


def test_missing_api_key_returns_configuration_error() -> None:
    client = make_client()
    client.app.state.config.openai_api_key = ""

    response = client.post(
        "/generate",
        json={"raw_transcript": "content"},
    )

    assert response.status_code == 500
    assert "OPENAI_API_KEY" in response.json()["detail"]


def test_malformed_preview_html_returns_controlled_error() -> None:
    client = make_client()

    async def fake_generate_lecture_content(
        title: str,
        transcript: str,
        youtube_url: str | None,
        request_id: str | None = None,
    ) -> str:
        return "Lecture content"

    async def fake_generate_preview_html(
        title: str,
        lecture_content: str,
        youtube_url: str | None,
        request_id: str | None = None,
    ) -> str:
        raise ModelOutputError("Stage 2 did not return a complete HTML document.")

    client.app.state.services.generate_lecture_content = fake_generate_lecture_content
    client.app.state.services.generate_preview_html = fake_generate_preview_html

    response = client.post(
        "/generate",
        json={"raw_transcript": "content"},
    )

    assert response.status_code == 502
    assert "Stage 2 did not return a complete HTML document." in response.json()["detail"]
