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
        json={"raw_transcript": "hello world", "output_format": "markdown"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["lecture_format"] == "markdown"
    assert data["output_format"] == "markdown"
    assert data["download_filename"].endswith(".md")
    assert data["download_content"].startswith("# Clean lecture")
    assert "<html" in data["preview_html"].lower()


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
        json={"raw_transcript": "hello world", "output_format": "markdown"},
    )

    assert response.status_code == 200
    markdown = response.json()["download_content"]
    assert "## CS408 Lecture01" in markdown
    assert "## Introduction" in markdown
    assert "## Main Concepts and Explanation" in markdown
    assert "### Human Computer Interaction" in markdown


def test_html_output_generates_html_download() -> None:
    client = make_client()

    async def fake_generate_lecture_markdown(
        title: str,
        transcript: str,
        youtube_url: str | None,
        request_id: str | None = None,
    ) -> str:
        assert request_id is not None
        return "# Lecture"

    async def fake_generate_preview_html(
        title: str,
        lecture_markdown: str,
        youtube_url: str | None,
        request_id: str | None = None,
    ) -> str:
        assert title == "lecture"
        assert lecture_markdown == "# Lecture"
        assert request_id is not None
        return "<!DOCTYPE html><html><body><h1>Lecture</h1></body></html>"

    client.app.state.services.generate_lecture_markdown = fake_generate_lecture_markdown
    client.app.state.services.generate_preview_html = fake_generate_preview_html

    response = client.post(
        "/generate",
        json={"raw_transcript": "hello world", "output_format": "html"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["output_format"] == "html"
    assert data["download_filename"].endswith(".html")
    assert data["download_content"].startswith("<!DOCTYPE html>")


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
            "output_format": "markdown",
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
        json={"youtube_url": "https://www.youtube.com/watch?v=abc123def45", "output_format": "markdown"},
    )

    assert response.status_code == 400
    assert "Paste the transcript manually to continue" in response.json()["detail"]


def test_missing_preview_prompt_does_not_block_markdown_output() -> None:
    client = make_client()
    client.app.state.config.preview_prompt_path = Path("/tmp/does-not-exist.txt")

    async def fake_generate_lecture_markdown(
        title: str,
        transcript: str,
        youtube_url: str | None,
        request_id: str | None = None,
    ) -> str:
        return "# Lecture"

    client.app.state.services.generate_lecture_markdown = fake_generate_lecture_markdown

    response = client.post(
        "/generate",
        json={"raw_transcript": "content", "output_format": "markdown"},
    )

    assert response.status_code == 200
    assert response.json()["output_format"] == "markdown"


def test_malformed_html_renderer_returns_controlled_error() -> None:
    client = make_client()

    async def fake_generate_lecture_markdown(
        title: str,
        transcript: str,
        youtube_url: str | None,
        request_id: str | None = None,
    ) -> str:
        return "# Lecture content"

    async def fake_generate_preview_html(
        title: str,
        lecture_markdown: str,
        youtube_url: str | None,
        request_id: str | None = None,
    ) -> str:
        raise ModelOutputError("HTML renderer did not return a complete HTML document.")

    client.app.state.services.generate_lecture_markdown = fake_generate_lecture_markdown
    client.app.state.services.generate_preview_html = fake_generate_preview_html

    response = client.post(
        "/generate",
        json={"raw_transcript": "content", "output_format": "html"},
    )

    assert response.status_code == 502
    assert "HTML renderer did not return a complete HTML document." in response.json()["detail"]
