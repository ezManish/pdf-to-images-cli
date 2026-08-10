"""
test_pdf_to_image.py

pytest unit & API integration test suite for pdf-to-images-cli library.
"""

import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from guide_text import GUIDE_TEXT
from pdf_to_image import _parse_page_ranges, main, pdf_to_images, SUPPORTED_FORMATS, PILLOW_FALLBACK_FORMATS
from api import app, _sanitize_filename, MAX_FILE_SIZE_BYTES


def test_guide_text_module_loaded():
    """Verify that GUIDE_TEXT is non-empty and properly imported from guide_text module."""
    assert isinstance(GUIDE_TEXT, str)
    assert len(GUIDE_TEXT) > 500
    assert "pdf-to-images-cli Complete User Guide" in GUIDE_TEXT


def test_parse_page_ranges_valid():
    assert _parse_page_ranges("1", 10) == [0]
    assert _parse_page_ranges("1, 3, 5", 10) == [0, 2, 4]
    assert _parse_page_ranges("1-3, 5", 10) == [0, 1, 2, 4]
    assert _parse_page_ranges(" 2 - 4 ", 10) == [1, 2, 3]


def test_parse_page_ranges_invalid_non_integer():
    with pytest.raises(ValueError, match="must be integers"):
        _parse_page_ranges("abc", 10)

    with pytest.raises(ValueError, match="must be integers"):
        _parse_page_ranges("1-xyz", 10)


def test_parse_page_ranges_invalid_bounds():
    with pytest.raises(ValueError, match="start \\(5\\) is after end \\(2\\)"):
        _parse_page_ranges("5-2", 10)

    with pytest.raises(ValueError, match="Invalid page number '15'"):
        _parse_page_ranges("15", 10)


def test_format_collections():
    assert "png" in SUPPORTED_FORMATS
    assert "jpg" in SUPPORTED_FORMATS
    assert "webp" in PILLOW_FALLBACK_FORMATS
    assert "tiff" in PILLOW_FALLBACK_FORMATS


def test_nonexistent_pdf_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        pdf_to_images("nonexistent_file_xyz_123.pdf")


def test_cli_exit_codes():
    """Verify distinct CLI exit codes."""
    assert main(["--guide"]) == 0
    assert main(["nonexistent_file_xyz_999.pdf"]) == 10  # File Not Found
    sample_pdf = Path("Participation_Certificates.pdf")
    if sample_pdf.exists():
        assert main([str(sample_pdf), "--workers", "0"]) == 1  # Validation Error


def test_parameter_validation_clamping():
    sample_pdf = Path("Participation_Certificates.pdf")
    if not sample_pdf.exists():
        return

    with pytest.raises(ValueError, match="Invalid workers count '0'"):
        pdf_to_images(sample_pdf, workers=0)

    with pytest.raises(ValueError, match="Invalid DPI parameter '-50'"):
        pdf_to_images(sample_pdf, dpi=-50)

    with pytest.raises(ValueError, match="Invalid quality parameter '150'"):
        pdf_to_images(sample_pdf, quality=150)


def test_path_traversal_sanitization():
    """Verify path traversal characters are sanitized from uploaded filenames."""
    assert _sanitize_filename("../../etc/passwd.pdf") == "passwd.pdf"
    assert _sanitize_filename("C:\\Windows\\System32\\document.pdf") == "document.pdf"
    with pytest.raises(Exception):
        _sanitize_filename("script.exe")


def test_api_endpoints():
    """Integration tests for FastAPI endpoints with TestClient."""
    client = TestClient(app)

    # Health check endpoint
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}

    # File size limit check (413 Payload Too Large)
    fake_large_payload = b"%" + b"0" * (MAX_FILE_SIZE_BYTES + 100)
    response = client.post(
        "/convert",
        files={"file": ("large_doc.pdf", fake_large_payload, "application/pdf")},
    )
    assert response.status_code == 413
