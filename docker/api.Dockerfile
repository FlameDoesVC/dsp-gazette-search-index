# Django API image. Python pinned to the version the project develops against.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# poppler-utils supplies pdftotext for the attachment extraction ladder.
# No rasterization tooling is needed: scanned PDFs go to Claude as files.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./

RUN pip install -r requirements.txt \
    && pip install gunicorn uvicorn

EXPOSE 8000


FROM base AS dev
# Source arrives via bind mount; Django's autoreloader handles the rest.
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]


FROM base AS prod
COPY . .
CMD ["gunicorn", "beynunehcheh.asgi:application", \
     "--worker-class=uvicorn.workers.UvicornWorker", \
     "--workers=3", "--bind=0.0.0.0:8000"]
