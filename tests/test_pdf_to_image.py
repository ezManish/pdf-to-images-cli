"""
test_pdf_to_image.py

pytest unit test suite for pdf-to-images-cli library.
"""

from pathlib import Path
import pytest

from pdf_to_image import _parse_page_ranges, pdf_to_images, SUPPORTED_FORMATS, PILLOW_FALLBACK_FORMATS


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


def test_pdf_conversion_if_sample_exists():
    sample_pdf = Path("Participation_Certificates.pdf")
    if sample_pdf.exists():
        results = pdf_to_images(sample_pdf, pages="1-2", fmt="png", dpi=100)
        assert isinstance(results, list)
        assert len(results) == 2
        for p in results:
            assert p.exists()


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
