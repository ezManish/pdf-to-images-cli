# Production Dockerfile for pdf-to-images-cli REST API microservice
FROM python:3.11-slim

WORKDIR /app

# Prevent Python from writing pyc files and buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Copy project files
COPY pyproject.toml README.md guide_text.py pdf_to_image.py api.py LICENSE /app/

# Install package with REST API dependencies
RUN pip install --no-cache-dir .[api]

# Expose port 8000
EXPOSE 8000

# Start FastAPI server
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
