"""
api.py

FastAPI Web Service for pdf-to-images-cli.
Turn PDF conversion into a SaaS backend REST API.

Run locally:
    uvicorn api:app --reload --port 8000
"""

from __future__ import annotations

import base64
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import List, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from pdf_to_image import pdf_to_images

app = FastAPI(
    title="Pdf2Pix Conversion API",
    description="High-Performance PDF Page to Image Conversion REST Microservice",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Enable CORS for SaaS Web Frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _cleanup_temp_file(path: Path) -> None:
    """Helper to remove temporary files after sending streaming HTTP response."""
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


@app.get("/", tags=["General"])
def read_root():
    """Root endpoint returning API service status and links to docs."""
    return {
        "service": "Pdf2Pix Conversion API",
        "version": "1.0.0",
        "docs": "/docs",
        "status": "operational",
    }


@app.get("/health", tags=["General"])
def health_check():
    """Healthcheck endpoint for monitoring container/cloud deployments."""
    return {"status": "healthy"}


@app.post("/convert", tags=["Conversion"])
async def convert_pdf_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="PDF file to convert"),
    format: str = Form("png", description="Output format (png, jpg, webp, tiff, etc)"),
    dpi: int = Form(200, description="DPI render resolution (e.g. 150, 200, 300)"),
    pages: Optional[str] = Form(None, description="Page range string like '1,3,5-8'"),
    combine: bool = Form(False, description="Stack pages into a single vertical image"),
    quality: int = Form(90, description="JPEG quality (1-100)"),
    grayscale: bool = Form(False, description="Render in grayscale"),
    optimize: bool = Form(False, description="Apply additional image compression"),
    workers: int = Form(4, description="Parallel worker processes for fast rendering"),
):
    """
    Upload a PDF file and receive converted images compressed in a ZIP archive (or direct single image file if combined).
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are supported.",
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        pdf_input_path = tmp_path / file.filename
        
        with open(pdf_input_path, "wb") as f:
            content = await file.read()
            f.write(content)

        output_folder = tmp_path / "output"
        output_folder.mkdir(exist_ok=True)

        try:
            results = pdf_to_images(
                pdf_path=pdf_input_path,
                output_dir=output_folder,
                fmt=format,
                dpi=dpi,
                pages=pages,
                combine=combine,
                quality=quality,
                grayscale=grayscale,
                optimize=optimize,
                workers=workers,
            )
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

        pdf_stem = pdf_input_path.stem

        # If combine mode, return the single combined image directly
        if combine and isinstance(results, Path):
            persistent_img = Path(tempfile.gettempdir()) / f"{pdf_stem}_{os.urandom(4).hex()}{results.suffix}"
            shutil.copy(results, persistent_img)
            background_tasks.add_task(_cleanup_temp_file, persistent_img)

            return FileResponse(
                path=persistent_img,
                media_type=f"image/{format.lower()}",
                filename=results.name,
            )

        # Otherwise, package images into a ZIP archive for client download
        zip_path = tmp_path / f"{pdf_stem}_converted.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
            if isinstance(results, list):
                for img_path in results:
                    zip_file.write(img_path, arcname=img_path.name)
            elif isinstance(results, Path):
                zip_file.write(results, arcname=results.name)

        persistent_zip = Path(tempfile.gettempdir()) / f"{pdf_stem}_{os.urandom(4).hex()}.zip"
        shutil.copy(zip_path, persistent_zip)
        background_tasks.add_task(_cleanup_temp_file, persistent_zip)

        return FileResponse(
            path=persistent_zip,
            media_type="application/zip",
            filename=f"{pdf_stem}_images.zip",
        )


@app.post("/convert/json", tags=["Conversion"])
async def convert_pdf_json_endpoint(
    file: UploadFile = File(..., description="PDF file to convert"),
    format: str = Form("png", description="Output format"),
    dpi: int = Form(150, description="DPI resolution"),
    pages: Optional[str] = Form(None, description="Page spec e.g. '1-3'"),
    combine: bool = Form(False, description="Combine into single vertical image"),
    grayscale: bool = Form(False, description="Grayscale rendering"),
    workers: int = Form(4, description="Parallel worker processes"),
):
    """
    SaaS API endpoint: Upload a PDF file and return base64-encoded images in JSON format.
    Ideal for direct web client rendering and backend integrations.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are supported.",
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        pdf_input_path = tmp_path / file.filename

        with open(pdf_input_path, "wb") as f:
            content = await file.read()
            f.write(content)

        output_folder = tmp_path / "output"
        output_folder.mkdir(exist_ok=True)

        try:
            results = pdf_to_images(
                pdf_path=pdf_input_path,
                output_dir=output_folder,
                fmt=format,
                dpi=dpi,
                pages=pages,
                combine=combine,
                grayscale=grayscale,
                workers=workers,
            )
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

        images_payload = []
        target_list = [results] if isinstance(results, Path) else results

        for img_path in target_list:
            with open(img_path, "rb") as img_file:
                b64_str = base64.b64encode(img_file.read()).decode("utf-8")
                images_payload.append({
                    "filename": img_path.name,
                    "format": format.lower(),
                    "size_bytes": img_path.stat().st_size,
                    "base64": f"data:image/{format.lower()};base64,{b64_str}",
                })

        return JSONResponse(content={
            "success": True,
            "filename": file.filename,
            "page_count": len(images_payload),
            "images": images_payload,
        })
