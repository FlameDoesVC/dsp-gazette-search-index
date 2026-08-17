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

# llama-cpp-python and the huggingface downloader exist for running the
# translation model on the same machine. Inference lives on the GPU host and is
# reached over OLLAMA_URL, so they are filtered out here: building llama.cpp in
# this image costs many minutes and buys nothing.
#
# Phase 1 replaces this filter with a proper requirements.txt /
# requirements-local-llm.txt split, at which point this becomes a plain
# `pip install -r requirements.txt`.
RUN grep -viE '^(llama[-_]cpp[-_]python|huggingface[-_]hub|hf-xet)([=<>~ ]|$)' \
        requirements.txt > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt \
    && pip install \
        "psycopg[binary]" \
        dj-database-url \
        django-ninja \
        gunicorn \
        uvicorn \
        anthropic \
        python-docx

EXPOSE 8000


FROM base AS dev
# Source arrives via bind mount; Django's autoreloader handles the rest.
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]


FROM base AS prod
COPY . .
CMD ["gunicorn", "beynunehcheh.asgi:application", \
     "--worker-class=uvicorn.workers.UvicornWorker", \
     "--workers=3", "--bind=0.0.0.0:8000"]
