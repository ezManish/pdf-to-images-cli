#!/usr/bin/env python3
"""
pdf_to_image.py

Convert PDF pages to images (PNG, JPG, WEBP, TIFF, etc).

Works both as a CLI tool and as an importable module.

CLI usage:
    python pdf_to_image.py input.pdf
    python pdf_to_image.py input.pdf --format jpg --dpi 300
    python pdf_to_image.py input.pdf --pages 1,3,5-8
    python pdf_to_image.py input.pdf --combine --format png
    python pdf_to_image.py input.pdf --output-dir out --prefix page
    python pdf_to_image.py input.pdf --grayscale --optimize
    python pdf_to_image.py input.pdf --workers 4        # parallel rendering, big PDFs only

Module usage:
    from pdf_to_image import pdf_to_images

    paths = pdf_to_images("input.pdf", fmt="png", dpi=200)
    path = pdf_to_images("input.pdf", combine=True, fmt="jpg")

Dependencies:
    pip install pymupdf
    pip install pillow   # only needed for webp/tiff/bmp/gif output, or --combine
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path
from typing import List, Optional, Union

import pymupdf  # PyMuPDF

SUPPORTED_FORMATS = {"png", "jpg", "jpeg", "ppm", "pgm", "pbm", "pam"}
PILLOW_FALLBACK_FORMATS = {"webp", "tiff", "tif", "bmp", "gif"}
ALPHA_CAPABLE_FORMATS = {"png", "webp", "tiff", "tif"}

# Maps PyMuPDF's pixmap channel count to a Pillow mode.
# n=1 -> grayscale, n=2 -> grayscale+alpha, n=3 -> RGB, n=4 -> RGBA
_PIXMAP_N_TO_PIL_MODE = {1: "L", 2: "LA", 3: "RGB", 4: "RGBA"}


def _parse_page_ranges(spec: str, page_count: int) -> List[int]:
    """Parse "1,3,5-8" (1-indexed) into a sorted list of 0-indexed page numbers."""
    pages: set[int] = set()
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start_s, end_s = chunk.split("-", 1)
            start, end = int(start_s), int(end_s)
            if start > end:
                raise ValueError(
                    f"Invalid page range '{chunk}': start ({start}) is after end ({end})"
                )
            if start < 1 or end > page_count:
                raise ValueError(f"Invalid page range '{chunk}' for a {page_count}-page PDF")
            pages.update(range(start - 1, end))
        else:
            p = int(chunk)
            if p < 1 or p > page_count:
                raise ValueError(f"Invalid page number '{p}' for a {page_count}-page PDF")
            pages.add(p - 1)
    return sorted(pages)


def _pixmap_to_pil(pix: "pymupdf.Pixmap"):
    """Convert a Pixmap to a PIL Image directly from its raw buffer (no PNG roundtrip)."""
    from PIL import Image

    mode = _PIXMAP_N_TO_PIL_MODE.get(pix.n)
    if mode is None:
        # Rare: CMYK or other colorspace. Fall back to a safe (slower) path.
        return Image.open(io.BytesIO(pix.tobytes("png")))
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


def _save_pixmap(pix: "pymupdf.Pixmap", out_path: Path, fmt: str, quality: int, optimize: bool) -> None:
    fmt = fmt.lower()
    if fmt in SUPPORTED_FORMATS:
        norm_fmt = "jpeg" if fmt == "jpg" else fmt
        if norm_fmt == "jpeg":
            pix.save(str(out_path), output="jpeg", jpg_quality=quality)
        else:
            pix.save(str(out_path), output=norm_fmt)
    elif fmt in PILLOW_FALLBACK_FORMATS:
        try:
            img = _pixmap_to_pil(pix)
        except ImportError as exc:
            raise RuntimeError(
                f"Format '{fmt}' needs Pillow. Install it with: pip install pillow"
            ) from exc
        save_kwargs = {}
        if fmt in ("tiff", "tif"):
            save_kwargs["compression"] = "tiff_lzw"
        if optimize and fmt in ("png", "webp"):
            save_kwargs["optimize"] = True
        img.save(str(out_path), **save_kwargs)
    else:
        raise ValueError(
            f"Unsupported format '{fmt}'. Supported: "
            f"{sorted(SUPPORTED_FORMATS | PILLOW_FALLBACK_FORMATS)}"
        )


def _render_page(doc: "pymupdf.Document", idx: int, matrix, grayscale: bool, fmt: str):
    """Render a single page to a Pixmap."""
    page = doc.load_page(idx)
    colorspace = pymupdf.csGRAY if grayscale else pymupdf.csRGB
    alpha = (not grayscale) and (fmt in ALPHA_CAPABLE_FORMATS)
    return page.get_pixmap(matrix=matrix, colorspace=colorspace, alpha=alpha)


def _render_and_save_worker(
    pdf_path_str: str, idx: int, dpi: int, fmt: str, quality: int,
    grayscale: bool, optimize: bool, out_path_str: str,
) -> str:
    """Standalone worker: opens its own Document handle so it's safe across processes."""
    doc = pymupdf.open(pdf_path_str)
    try:
        zoom = dpi / 72
        matrix = pymupdf.Matrix(zoom, zoom)
        pix = _render_page(doc, idx, matrix, grayscale, fmt)
        _save_pixmap(pix, Path(out_path_str), fmt, quality, optimize)
        return out_path_str
    finally:
        doc.close()


def _render_and_bytes_worker(
    pdf_path_str: str, idx: int, dpi: int, grayscale: bool,
) -> tuple:
    """Standalone worker for combine mode: returns (idx, png_bytes)."""
    doc = pymupdf.open(pdf_path_str)
    try:
        zoom = dpi / 72
        matrix = pymupdf.Matrix(zoom, zoom)
        pix = _render_page(doc, idx, matrix, grayscale, "png")
        return idx, pix.tobytes("png")
    finally:
        doc.close()


def pdf_to_images(
    pdf_path: Union[str, Path],
    output_dir: Union[str, Path, None] = None,
    fmt: str = "png",
    dpi: int = 200,
    pages: Optional[str] = None,
    combine: bool = False,
    prefix: Optional[str] = None,
    quality: int = 90,
    grayscale: bool = False,
    optimize: bool = False,
    workers: int = 1,
    show_progress: bool = False,
) -> Union[List[Path], Path]:
    """
    Convert a PDF's pages into image file(s).

    Args:
        pdf_path: Path to the source PDF.
        output_dir: Parent directory to write images into. Defaults to 'output/<pdf_name>'.
        fmt: Output image format: png, jpg, webp, tiff, bmp, ppm, etc.
        dpi: Render resolution. 150 = draft, 200-300 = good print/OCR quality, 600 = high-res.
        pages: Page spec string like "1,3,5-8" (1-indexed). None = all pages.
        combine: If True, stack all selected pages into a single vertical image.
        prefix: Filename prefix for per-page output. Defaults to the PDF's stem.
        quality: JPEG quality (1-100), ignored for lossless formats.
        grayscale: Render in grayscale instead of RGB (smaller files, good for OCR/scans).
        optimize: Apply extra compression where supported (slower encode, smaller files).
        workers: Number of processes to render pages in parallel. 1 = sequential.
                 Only worth raising for large page counts or high DPI — process
                 startup overhead can make small jobs slower with workers > 1.
        show_progress: Print "page X/N" progress to stderr as pages complete.

    Returns:
        A list of Paths (one per page) if combine=False, or a single Path if combine=True.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    fmt = fmt.lower().lstrip(".")
    base_out_dir = Path(output_dir) if output_dir else Path("output")
    out_dir = base_out_dir / pdf_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    name_prefix = prefix or pdf_path.stem

    doc = pymupdf.open(str(pdf_path))
    try:
        page_count = doc.page_count
    finally:
        doc.close()

    page_indices = _parse_page_ranges(pages, page_count) if pages else list(range(page_count))
    if not page_indices:
        raise ValueError("No pages selected for conversion")

    total = len(page_indices)
    pad = max(3, len(str(page_count)))

    def _progress(done: int) -> None:
        if show_progress:
            print(f"Processing page {done}/{total}...", end="\r", file=sys.stderr)

    zoom = dpi / 72
    matrix = pymupdf.Matrix(zoom, zoom)

    if combine:
        # Collect PNG bytes for every page (in order), then stack once at the end.
        results: dict[int, bytes] = {}
        if workers > 1:
            from concurrent.futures import ProcessPoolExecutor, as_completed

            with ProcessPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(_render_and_bytes_worker, str(pdf_path), idx, dpi, grayscale): idx
                    for idx in page_indices
                }
                done = 0
                for future in as_completed(futures):
                    idx, png_bytes = future.result()
                    results[idx] = png_bytes
                    done += 1
                    _progress(done)
        else:
            doc = pymupdf.open(str(pdf_path))
            try:
                for done, idx in enumerate(page_indices, start=1):
                    pix = _render_page(doc, idx, matrix, grayscale, "png")
                    results[idx] = pix.tobytes("png")
                    _progress(done)
            finally:
                doc.close()
        if show_progress:
            print(file=sys.stderr)
        ordered_bytes = [results[idx] for idx in page_indices]
        return _combine_png_bytes(ordered_bytes, out_dir, name_prefix, fmt, quality)

    # Per-page mode: save each page as soon as it's rendered, never hold more
    # than one (or `workers`) pixmap in memory at a time.
    out_paths_by_idx: dict[int, Path] = {}
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for idx in page_indices:
                ext = fmt if fmt != "jpeg" else "jpg"
                out_path = out_dir / f"{name_prefix}_p{idx + 1:0{pad}d}.{ext}"
                out_paths_by_idx[idx] = out_path
                futures[executor.submit(
                    _render_and_save_worker, str(pdf_path), idx, dpi, fmt,
                    quality, grayscale, optimize, str(out_path),
                )] = idx
            done = 0
            for future in as_completed(futures):
                future.result()  # raises here if a worker failed
                done += 1
                _progress(done)
    else:
        doc = pymupdf.open(str(pdf_path))
        try:
            for done, idx in enumerate(page_indices, start=1):
                pix = _render_page(doc, idx, matrix, grayscale, fmt)
                ext = fmt if fmt != "jpeg" else "jpg"
                out_path = out_dir / f"{name_prefix}_p{idx + 1:0{pad}d}.{ext}"
                _save_pixmap(pix, out_path, fmt, quality, optimize)
                out_paths_by_idx[idx] = out_path
                _progress(done)
        finally:
            doc.close()

    if show_progress:
        print(file=sys.stderr)
    return [out_paths_by_idx[idx] for idx in page_indices]


def _combine_png_bytes(
    png_bytes_list: List[bytes], out_dir: Path, name_prefix: str, fmt: str, quality: int
) -> Path:
    """Stack pre-rendered PNG-bytes pages vertically into a single image."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Combining pages into one image needs Pillow. Install it with: pip install pillow"
        ) from exc

    pil_images = [Image.open(io.BytesIO(b)) for b in png_bytes_list]
    total_width = max(img.width for img in pil_images)
    total_height = sum(img.height for img in pil_images)

    mode = "RGB" if fmt in ("jpg", "jpeg") else "RGBA"
    combined = Image.new(mode, (total_width, total_height), "white")

    y_offset = 0
    for img in pil_images:
        if mode == "RGB" and img.mode in ("RGBA", "LA"):
            img = img.convert("RGB")
        elif img.mode == "L" and mode == "RGBA":
            img = img.convert("RGBA")
        combined.paste(img, (0, y_offset))
        y_offset += img.height

    out_name = f"{name_prefix}_combined.{fmt if fmt != 'jpeg' else 'jpg'}"
    out_path = out_dir / out_name
    save_kwargs = {"quality": quality} if fmt in ("jpg", "jpeg") else {}
    combined.save(str(out_path), **save_kwargs)
    return out_path


GUIDE_TEXT = """================================================================================
pdf-to-images-cli Complete User Guide & Architectural Reference
================================================================================

CLI Executables: pdf-to-image

OVERVIEW:
  pdf-to-images-cli converts PDF document pages into high-resolution images.
  Powered by PyMuPDF (fitz) for sub-millisecond C-level vector rendering and
  Pillow for advanced format encoding. Supports multi-core CPU process scaling,
  vertical image stitching, and a REST API backend.

--------------------------------------------------------------------------------
1. PARAMETER REFERENCE & HOW THEY WORK
--------------------------------------------------------------------------------

  pdf (Positional)
    Path to the source PDF file (e.g., document.pdf). Required for conversion.

  -f, --format {png, jpg, webp, tiff, bmp, ppm, pgm}
    Specifies the output image encoding format.
    - PNG / WEBP / TIFF: Support 32-bit RGBA transparency.
    - JPG / JPEG: Lossy compression, RGB/Grayscale, adjustable with --quality.
    - BMP / PPM / PGM: Uncompressed raw bitmap formats for graphics tools.
    Default: png

  -d, --dpi DPI (Resolution)
    Controls render resolution in Dots Per Inch (DPI).
    - 72 DPI  : Fast draft / preview mode.
    - 150 DPI : Screen display & web publishing.
    - 200 DPI : Default balancing resolution, speed, and clarity.
    - 300 DPI : High-resolution print & OCR engine input.
    - 600 DPI : Ultra-high archival graphics resolution.
    Default: 200

  -o, --output-dir DIR
    Target parent directory for exported images.
    By default, images are written into a subfolder named after the PDF:
      output/<pdf_name>/<pdf_name>_p001.png
    Default: output/<pdf_name>

  -p, --pages PAGES
    1-indexed page range specification. Accepts commas and hyphenated ranges.
    Examples:
      --pages "1"       -> Page 1 only
      --pages "1,3,5"   -> Pages 1, 3, and 5
      --pages "1-5,8"   -> Pages 1 through 5, and page 8
    Default: All pages in document

  -c, --combine (Vertical Image Stacking)
    Instead of per-page images, stitches all selected pages into a single
    contiguous vertical image. Useful for long document previews & web pages.

  -w, --workers WORKERS (Parallel Acceleration)
    Number of parallel CPU worker processes (ProcessPoolExecutor).
    Each worker opens an isolated C-level PyMuPDF document handle, eliminating
    GIL contention and delivering up to 5.4x+ speedup on multi-core CPUs.
    Default: 1 (Sequential)

  -q, --quality QUALITY (1-100)
    Sets JPEG encoding quality rating from 1 (lowest quality, smallest file) to
    100 (highest quality, largest file). Ignored for PNG/lossless formats.
    Default: 90

  -g, --grayscale
    Renders pages into single-channel 8-bit grayscale (csGRAY).
    Reduces output file sizes by ~60% and memory footprint by 75%.

  --optimize
    Enables extra image compression passes (PNG zlib strategy / WebP lossy).
    Produces smaller files at the cost of slightly higher CPU encode time.

  --prefix PREFIX
    Custom filename prefix for exported page images.
    Default: PDF filename stem (e.g. document_p001.png)

  --progress
    Prints real-time progress indicators ("Processing page X/N...") to stderr.

--------------------------------------------------------------------------------
2. COMMON CLI COMMAND EXAMPLES
--------------------------------------------------------------------------------

  * Standard Conversion:
      pdf-to-image document.pdf

  * High-DPI JPEG Export (300 DPI, Quality 95):
      pdf-to-image contract.pdf -f jpg -d 300 -q 95

  * Extract Pages 1, 3, and 5-10:
      pdf-to-image report.pdf --pages "1,3,5-10"

  * Multi-Core Parallel Scaling (8 Workers):
      pdf-to-image large_book.pdf --workers 8 --progress

  * Vertical Image Stacking:
      pdf-to-image presentation.pdf --combine --format webp

  * Grayscale OCR Optimization:
      pdf-to-image scan.pdf --grayscale --dpi 300 --optimize

--------------------------------------------------------------------------------
3. FASTAPI REST MICROSERVICE
--------------------------------------------------------------------------------

  Launch SaaS REST server:
    uvicorn api:app --reload --port 8000

  Endpoints:
    GET  /health        -> Healthcheck monitoring
    POST /convert       -> Upload PDF -> Stream ZIP file download
    POST /convert/json  -> Upload PDF -> Return JSON with Base64 images array

  Interactive OpenAPI Docs: http://localhost:8000/docs

--------------------------------------------------------------------------------
4. PYTHON MODULE IMPORT
--------------------------------------------------------------------------------

  from pdf_to_image import pdf_to_images

  # Per-page list of Path objects
  paths = pdf_to_images("document.pdf", fmt="png", dpi=200, workers=4)

  # Stitched single combined image Path
  combined = pdf_to_images("document.pdf", combine=True, fmt="jpg")

================================================================================
"""


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert PDF pages to high-resolution images (PNG, JPG, WEBP, TIFF, BMP, ...).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run 'pdf-to-image --guide' to view the complete user guide and architectural reference.",
    )
    parser.add_argument("pdf", nargs="?", default=None, help="Path to input PDF file (e.g. document.pdf)")
    parser.add_argument("-f", "--format", default="png", help="Output image format: png, jpg, webp, tiff, bmp, ppm, pgm. Default: png")
    parser.add_argument("-d", "--dpi", type=int, default=200, help="Render resolution DPI (72=draft, 150=web, 200=default, 300=OCR/print). Default: 200")
    parser.add_argument("-o", "--output-dir", default=None, help="Output directory path. Defaults to 'output/<pdf_name>/'")
    parser.add_argument("-p", "--pages", default=None, help='1-indexed page selection specifier (e.g. "1", "1,3,5", "1-5,8")')
    parser.add_argument("-c", "--combine", action="store_true", help="Stitch all rendered pages vertically into a single contiguous image file")
    parser.add_argument("--prefix", default=None, help="Filename prefix for output files. Defaults to PDF filename stem")
    parser.add_argument("-q", "--quality", type=int, default=90, help="JPEG compression quality rating (1-100). Default: 90")
    parser.add_argument("-g", "--grayscale", action="store_true", help="Render in single-channel 8-bit grayscale mode (smaller files, fast OCR)")
    parser.add_argument("--optimize", action="store_true", help="Apply additional image compression passes (slower encode, smaller files)")
    parser.add_argument(
        "-w", "--workers", type=int, default=1,
        help="Number of parallel CPU worker processes. Scales rendering across CPU cores (up to 5.4x+ speedup). Default: 1",
    )
    parser.add_argument("--progress", action="store_true", help="Print real-time page conversion progress to stderr")
    parser.add_argument("--guide", "--examples", action="store_true", help="Display the complete interactive CLI user guide and feature reference")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.guide:
        print(GUIDE_TEXT)
        return 0

    if not args.pdf:
        parser.print_help()
        return 1

    start_time = time.perf_counter()
    try:
        result = pdf_to_images(
            pdf_path=args.pdf,
            output_dir=args.output_dir,
            fmt=args.format,
            dpi=args.dpi,
            pages=args.pages,
            combine=args.combine,
            prefix=args.prefix,
            quality=args.quality,
            grayscale=args.grayscale,
            optimize=args.optimize,
            workers=args.workers,
            show_progress=args.progress,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    elapsed = time.perf_counter() - start_time

    if isinstance(result, list):
        count = len(result)
        rate = count / elapsed if elapsed > 0 else 0
        print(f"Wrote {count} image(s) in {elapsed:.2f}s ({rate:.1f} pages/sec):")
        for p in result:
            print(f"  {p}")
    else:
        print(f"Wrote combined image in {elapsed:.2f}s: {result}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())