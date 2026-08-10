# pdf-to-images-cli

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PyMuPDF](https://img.shields.io/badge/Powered%20By-PyMuPDF-ff69b4.svg)](https://pymupdf.readthedocs.io/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![CI Pipeline](https://github.com/ezManish/pdf-to-images-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/ezManish/pdf-to-images-cli/actions)

High-performance Python package, CLI utility, and FastAPI REST microservice for converting PDF pages into high-resolution images (PNG, JPG, WEBP, TIFF, BMP) with multi-core parallel processing acceleration.

---

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [CLI Usage](#cli-usage)
- [Python API Usage](#python-api-usage)
- [FastAPI Microservice](#fastapi-microservice)
- [Supported Formats](#supported-formats)
- [Performance Benchmarks](#performance-benchmarks)
- [Docker Deployment](#docker-deployment)
- [CLI Reference](#cli-reference)
- [Repository Structure](#repository-structure)
- [License](#license)

---

## Features

- **Parallel Processing**: Multi-process rendering engine (`--workers N`) yielding up to **5.4x+ speedup** on multi-page PDF documents.
- **Pip Installable**: Standalone executable CLI commands (`pdf-to-image` and `pdf2pix`).
- **Production FastAPI SaaS Microservice**: Ready-to-deploy REST API endpoints (`/convert`, `/convert/json`, `/health`) with interactive OpenAPI documentation.
- **Multi-Format Export**: Native rendering for PNG, JPEG, WEBP, TIFF, BMP, GIF, PGM, PBM, PPM, and PAM.
- **Page Range Filtering**: Convert targeted page subsets using intuitive range syntax (`-p "1,3,5-10"`).
- **Vertical Image Stacking**: Stitches entire PDF documents into a single contiguous vertical composite image with `--combine`.
- **Execution Timing & Statistics**: Automatic CLI throughput metrics (`pages/sec`) and execution timing reports.
- **Zero-Copy Memory Safety**: Streaming worker handles prevent memory bloat even when processing massive PDFs.

---

## Installation

### Standard Pip Installation

Install directly via `pip`:

```bash
pip install pdf-to-images-cli
```

### Local Editable Installation

Install locally with full FastAPI REST backend support:

```bash
git clone https://github.com/ezManish/pdf-to-images-cli.git
cd pdf-to-images-cli
pip install -e .[all]
```

---

## CLI Usage

### Basic Conversion
Convert all PDF pages to PNG at 200 DPI (saved automatically into `output/<pdf_name>/`):

```bash
pdf-to-image document.pdf
```

Output:
```text
Wrote 113 image(s) in 4.59s (24.6 pages/sec):
  output/document/document_p001.png
  output/document/document_p002.png
  ...
```

### High-DPI JPEG Export
Render pages as high-quality JPEGs at 300 DPI with custom compression quality:

```bash
pdf-to-image contract.pdf --format jpg --dpi 300 --quality 95
```

### Targeted Page Subsets
Extract only specific pages (e.g. pages 1, 3, and 5 through 10):

```bash
pdf-to-image report.pdf --pages "1,3,5-10"
```

### Multi-Core Parallel Processing
Scale conversion across 8 parallel process workers with progress tracking:

```bash
pdf-to-image large_document.pdf --workers 8 --progress
```

### Vertical Stitched Composite
Combine all pages into a single vertical image file:

```bash
pdf-to-image presentation.pdf --combine --format webp
```

---

## Python API Usage

Import and use `pdf_to_images` directly inside Python code bases:

```python
from pathlib import Path
from pdf_to_image import pdf_to_images

# Convert PDF to per-page PNG images
image_paths = pdf_to_images(
    pdf_path="input.pdf",
    fmt="png",
    dpi=200,
    pages="1-5",
    workers=4
)
for path in image_paths:
    print(f"Generated: {path}")

# Stitched vertical composite mode
combined_path = pdf_to_images(
    pdf_path="input.pdf",
    combine=True,
    fmt="jpg",
    quality=90
)
print(f"Combined image saved to: {combined_path}")
```

---

## FastAPI Microservice

`pdf-to-images-cli` includes a built-in FastAPI application for cloud and SaaS microservices.

### Launch Server

```bash
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

Interactive API documentation is available at **`http://localhost:8000/docs`**.

### Endpoint Overview

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Healthcheck monitoring endpoint |
| `GET` | `/` | API service status & documentation metadata |
| `POST` | `/convert` | Multipart upload PDF -> Downloads converted `.zip` archive |
| `POST` | `/convert/json` | Multipart upload PDF -> Returns Base64 image payload in JSON |

### REST API Examples

#### Download ZIP Archive (`/convert`)
```bash
# On Windows PowerShell, use curl.exe explicitly:
curl.exe -X POST "http://localhost:8000/convert" \
  -F "file=@sample.pdf" \
  -F "pages=1-3" \
  -F "format=png" \
  -F "dpi=200" \
  -F "workers=4" \
  --output converted_images.zip
```

#### Base64 JSON Payload (`/convert/json`)
```bash
curl.exe -X POST "http://localhost:8000/convert/json" \
  -F "file=@sample.pdf" \
  -F "format=jpg" \
  -F "dpi=150"
```

---

## Supported Formats

| Format Extension | Output Color Space | Alpha Transparency | Recommended Use Case |
| :--- | :--- | :---: | :--- |
| `.png` | RGB / RGBA | Yes | Lossless web display & UI elements |
| `.jpg` / `.jpeg` | RGB / Grayscale | No | Standard web images & small file size |
| `.webp` | RGB / RGBA | Yes | Next-gen web optimization |
| `.tiff` / `.tif` | RGB / RGBA | Yes | Print publishing & archiving |
| `.bmp` | RGB | No | Uncompressed bitmap compatibility |
| `.gif` | Palette / RGB | Yes | Animated & indexed color compatibility |
| `.ppm` / `.pgm` / `.pam` | RGB / Grayscale | No | Linux graphics toolchains & netpbm |

---

## Performance Benchmarks

Empirical performance measured on a **113-page PDF document** (`Participation_Certificates.pdf`) rendered at **200 DPI**:

| Worker Count | Mode | Execution Time | Throughput | Acceleration Factor |
| :---: | :--- | :---: | :---: | :---: |
| **1** | Sequential | **24.84 s** | 4.55 pages/sec | `1.00x` (Baseline) |
| **2** | Parallel (2 Workers) | **13.04 s** | 8.66 pages/sec | `1.90x` |
| **4** | Parallel (4 Workers) | **7.06 s** | 16.00 pages/sec | `3.52x` |
| **8** | Parallel (8 Workers) | **4.59 s** | **24.64 pages/sec** | **`5.42x`** |

> **Summary**: Running **8 parallel worker processes** reduced rendering time from **24.84s to 4.59s**, delivering a **5.42x throughput acceleration**.

---

## Docker Deployment

Create a `Dockerfile` for containerized SaaS deployments:

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir .[all]

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build and launch container:

```bash
docker build -t pdf2pix-api .
docker run -d -p 8000:8000 pdf2pix-api
```

---

## CLI Reference

```text
usage: pdf-to-image [-h] [-f FORMAT] [-d DPI] [-o OUTPUT_DIR] [-p PAGES] [-c]
                    [--prefix PREFIX] [-q QUALITY] [-g] [--optimize]
                    [-w WORKERS] [--progress]
                    pdf

positional arguments:
  pdf                   Path to the input PDF file

options:
  -h, --help            Show this help message and exit
  -f, --format FORMAT   Output image format (png, jpg, webp, tiff, etc.). Default: png
  -d, --dpi DPI         Render resolution DPI. Default: 200
  -o, --output-dir DIR  Custom output directory. Default: output/<pdf_name>
  -p, --pages PAGES     1-indexed page specifier string e.g. "1,3,5-8"
  -c, --combine         Stack all pages vertically into a single image file
  --prefix PREFIX       Filename prefix. Default: input PDF stem name
  -q, --quality QUALITY JPEG quality rating (1-100). Default: 90
  -g, --grayscale       Render output image in grayscale mode
  --optimize            Enable additional image compression algorithms
  -w, --workers WORKERS Number of parallel worker processes. Default: 1
  --progress            Print real-time progress to stderr
```

---

## Repository Structure

```text
Pdf2Pix/
├── pdf_to_image.py     # Core conversion library & CLI entrypoint
├── api.py              # FastAPI microservice & REST endpoints
├── benchmark.py        # Performance benchmarking utility
├── pyproject.toml      # Package setup & entrypoint configuration
├── README.md           # Documentation
└── output/             # Generated output directory
```

---

## License

Distributed under the MIT License. See `LICENSE` for details.
