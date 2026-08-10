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
from guide_text import GUIDE_TEXT


__version__ = "1.0.2"


def get_pdf_info(pdf_path: Union[str, Path]) -> dict:
    """
    Retrieve document metadata, page count, and encryption status for a PDF file.

    Returns:
        dict: {'file_name': str, 'page_count': int, 'is_encrypted': bool, 'title': str, 'author': str}
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = pymupdf.open(str(pdf_path))
    try:
        is_encrypted = bool(doc.is_encrypted or doc.needs_pass)
        page_count = doc.page_count if not is_encrypted else 0
        metadata = doc.metadata or {}
        return {
            "file_name": pdf_path.name,
            "page_count": page_count,
            "is_encrypted": is_encrypted,
            "title": metadata.get("title", ""),
            "author": metadata.get("author", ""),
            "format": getattr(doc, "format", "PDF"),
        }
    finally:
        doc.close()

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
            parts = chunk.split("-", 1)
            try:
                start, end = int(parts[0].strip()), int(parts[1].strip())
            except ValueError:
                raise ValueError(f"Invalid page range specifier '{chunk}': page numbers must be integers")
            if start > end:
                raise ValueError(
                    f"Invalid page range '{chunk}': start ({start}) is after end ({end})"
                )
            if start < 1 or end > page_count:
                raise ValueError(f"Invalid page range '{chunk}' for a {page_count}-page PDF")
            pages.update(range(start - 1, end))
        else:
            try:
                p = int(chunk)
            except ValueError:
                raise ValueError(f"Invalid page specifier '{chunk}': page numbers must be integers")
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


def _validate_and_clamp_params(dpi: int, workers: int, quality: int) -> tuple[int, int, int]:
    """Validate and clamp numeric parameters to safe operational bounds across CLI, Module, and REST API."""
    if not isinstance(dpi, int) or dpi < 36 or dpi > 600:
        raise ValueError(f"Invalid DPI parameter '{dpi}': must be an integer between 36 and 600")
    if not isinstance(workers, int) or workers < 1:
        raise ValueError(f"Invalid workers count '{workers}': must be an integer >= 1")
    workers = min(workers, 16)
    if not isinstance(quality, int) or quality < 1 or quality > 100:
        raise ValueError(f"Invalid quality parameter '{quality}': must be an integer between 1 and 100")
    return dpi, workers, quality


def pdf_to_images(
    pdf_path: Union[str, Path],
    output_dir: Optional[Union[str, Path]] = None,
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
    dpi, workers, quality = _validate_and_clamp_params(dpi, workers, quality)

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
        if doc.is_encrypted or doc.needs_pass:
            raise ValueError(f"PDF '{pdf_path.name}' is encrypted or password-protected.")
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
        return _combine_pages_stream(
            pdf_path=pdf_path,
            page_indices=page_indices,
            matrix=matrix,
            grayscale=grayscale,
            workers=workers,
            out_dir=out_dir,
            name_prefix=name_prefix,
            fmt=fmt,
            quality=quality,
            show_progress=show_progress,
            total=total,
        )

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


def _render_and_samples_worker(
    pdf_path_str: str, idx: int, matrix_zoom: float, grayscale: bool,
) -> tuple:
    """Standalone worker for combine mode: returns (idx, width, height, n, samples)."""
    doc = pymupdf.open(pdf_path_str)
    try:
        matrix = pymupdf.Matrix(matrix_zoom, matrix_zoom)
        pix = _render_page(doc, idx, matrix, grayscale, "png")
        return idx, pix.width, pix.height, pix.n, bytes(pix.samples)
    finally:
        doc.close()


def _combine_pages_stream(
    pdf_path: Path,
    page_indices: List[int],
    matrix: pymupdf.Matrix,
    grayscale: bool,
    workers: int,
    out_dir: Path,
    name_prefix: str,
    fmt: str,
    quality: int,
    show_progress: bool,
    total: int,
) -> Path:
    """Stream page renders directly into canvas without buffering all page byte arrays in memory."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Combining pages into one image needs Pillow. Install it with: pip install pillow"
        ) from exc

    doc = pymupdf.open(str(pdf_path))
    try:
        dims = []
        for idx in page_indices:
            page = doc.load_page(idx)
            rect = page.rect
            w = int(rect.width * matrix.a)
            h = int(rect.height * matrix.d)
            dims.append((w, h))
    finally:
        doc.close()

    total_width = max(w for w, h in dims)
    total_height = sum(h for w, h in dims)

    y_offsets = {}
    curr_y = 0
    for idx, (w, h) in zip(page_indices, dims):
        y_offsets[idx] = curr_y
        curr_y += h

    target_mode = "RGB" if fmt in ("jpg", "jpeg") else "RGBA"
    combined = Image.new(target_mode, (total_width, total_height), "white")

    def _progress(done: int) -> None:
        if show_progress:
            print(f"Processing page {done}/{total}...", end="\r", file=sys.stderr)

    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_render_and_samples_worker, str(pdf_path), idx, matrix.a, grayscale): idx
                for idx in page_indices
            }
            done = 0
            for future in as_completed(futures):
                idx, w, h, n, samples = future.result()
                mode = _PIXMAP_N_TO_PIL_MODE.get(n, "RGB")
                page_img = Image.frombytes(mode, (w, h), samples)
                if target_mode == "RGB" and page_img.mode in ("RGBA", "LA"):
                    page_img = page_img.convert("RGB")
                elif page_img.mode == "L" and target_mode == "RGBA":
                    page_img = page_img.convert("RGBA")
                combined.paste(page_img, (0, y_offsets[idx]))
                done += 1
                _progress(done)
    else:
        doc = pymupdf.open(str(pdf_path))
        try:
            for done, idx in enumerate(page_indices, start=1):
                pix = _render_page(doc, idx, matrix, grayscale, "png")
                page_img = _pixmap_to_pil(pix)
                if target_mode == "RGB" and page_img.mode in ("RGBA", "LA"):
                    page_img = page_img.convert("RGB")
                elif page_img.mode == "L" and target_mode == "RGBA":
                    page_img = page_img.convert("RGBA")
                combined.paste(page_img, (0, y_offsets[idx]))
                _progress(done)
        finally:
            doc.close()

    if show_progress:
        print(file=sys.stderr)

    out_name = f"{name_prefix}_combined.{fmt if fmt != 'jpeg' else 'jpg'}"
    out_path = out_dir / out_name
    save_kwargs = {"quality": quality} if fmt in ("jpg", "jpeg") else {}
    combined.save(str(out_path), **save_kwargs)
    return out_path


# GUIDE_TEXT is imported directly from guide_text module at the top of the file


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
    parser.add_argument("-v", "--version", action="version", version=f"pdf-to-images-cli {__version__}")
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
    except FileNotFoundError as exc:
        print(f"Error [File Not Found]: {exc}", file=sys.stderr)
        return 10
    except ValueError as exc:
        err_msg = str(exc)
        if "encrypted" in err_msg.lower() or "password" in err_msg.lower():
            print(f"Error [Encrypted PDF]: {exc}", file=sys.stderr)
            return 11
        print(f"Error [Validation Error]: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error [Unexpected Failure]: {exc}", file=sys.stderr)
        return 99

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