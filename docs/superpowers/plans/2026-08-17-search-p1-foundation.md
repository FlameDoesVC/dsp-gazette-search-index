# Search Engine P1 Foundation - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the project to PostgreSQL and build a source-agnostic search index that serves working English lexical search over the existing iBay and gazette data.

**Architecture:** A new `search` app owns a denormalized `SearchDocument` table, LIST-partitioned by `source`, populated by pluggable per-source adapters. A new `core` app holds the translation client that is currently trapped inside `gazette`. Nothing in this phase calls a language model; documents are indexed from scraped fields only. The Dhivehi language pipeline, enrichment, attachments, and the HTTP API are later phases.

**Tech Stack:** Django 6.0.5, Python 3.12, PostgreSQL 18 (`tsvector`, `pg_trgm`, declarative partitioning), pytest + pytest-django, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-17-search-engine-design.md`

## Global Constraints

- Python 3.12; Django 6.0.5; PostgreSQL 18 (`postgres:18-alpine`).
- Two database aliases: `default` (pooled in production) and `direct` (never pooled). Management commands that stream or run DDL use `direct`. Source: spec 12.4.
- `SearchDocument` is LIST-partitioned by `source`. `doc_type` is mutable and must never be a partition key or part of a unique constraint. Source: spec 12.2.
- Identity is the natural key pair `(source, source_key)`. No foreign keys cross into source apps. Source rows are deactivated, never deleted. Source: spec 3.1.
- All GIN indexes are partial on `WHERE is_active`. Trigram indexes cover title columns only, never body text. Source: spec 4.1, 12.1.
- Body text is never stored on `SearchDocument`. Only titles, summaries and vectors. Source: spec 12.1.
- Streaming operations use `.iterator(chunk_size=500)`; never `list()` a queryset. Source: spec 12.4.
- Every task ends with a passing test run and a commit.
- Version control is **jj (Jujutsu)**, not git. Commit with `jj commit -m "..."`.

---

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `pytest.ini` | pytest-django configuration |
| `requirements-local-llm.txt` | llama-cpp-python and friends, split out of `requirements.txt` |
| `core/__init__.py`, `core/apps.py`, `core/models.py` | `TranslationCache`, moved from `gazette` |
| `core/translate.py` | Translation client, moved from `gazette` |
| `core/migrations/0001_initial.py` | Creates `core.TranslationCache` |
| `core/migrations/0002_copy_translation_cache.py` | Copies 676 rows from `gazette` |
| `search/__init__.py`, `search/apps.py` | App scaffolding |
| `search/models.py` | `Source`, `SearchDocument` |
| `search/migrations/0001_initial.py` | `Source` table (ordinary) |
| `search/migrations/0002_searchdocument.py` | Partitioned `SearchDocument` via raw SQL |
| `search/migrations/0003_seed_sources.py` | `ibay` and `gazette` registry rows |
| `search/adapters/base.py` | `SourceAdapter` protocol, `RawDocument`, `DocumentDraft`, registry |
| `search/adapters/ibay.py` | iBay adapter |
| `search/adapters/gazette.py` | Gazette adapter |
| `search/indexing.py` | Draft-to-row conversion and bulk upsert |
| `search/query.py` | Lexical search over `vector_en` |
| `search/management/commands/reindex.py` | Streaming reindex command |
| `search/tests/` | Test package for all of the above |
| `scripts/loadtest_seed.py` | Synthetic 100k document generator |

**Modified:**

| Path | Change |
|---|---|
| `requirements.txt` | Split out local-LLM deps; add Postgres, pytest, `dj-database-url` |
| `beynunehcheh/settings.py` | Env-driven secrets/debug/hosts, two DB aliases, new apps |
| `gazette/translate.py` | Becomes a re-export shim pointing at `core.translate` |
| `gazette/models.py` | Drops `TranslationCache` |
| `gazette/sync_service.py:15,20` | Import from `core`; `MAX_INDEX_PAGES` from settings |
| `gazette/management/commands/retranslate_gazette.py:6` | Import from `core` |
| `docker/api.Dockerfile` | Replace the `grep -v` filter with the real requirements split |

---

### Task 1: Test tooling and env-driven settings

**Files:**
- Create: `pytest.ini`, `requirements-local-llm.txt`
- Modify: `requirements.txt`, `beynunehcheh/settings.py:28-33`, `beynunehcheh/settings.py:84-89`, `docker/api.Dockerfile`
- Test: `search/tests/__init__.py` (placeholder package), `tests_settings/test_settings_env.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `settings.DATABASES['default']` and `settings.DATABASES['direct']`; `pytest` runnable via `./venv/bin/pytest`.

- [ ] **Step 1: Install the new dependencies**

```bash
cd /home/flame/random/beynunehcheh
./venv/bin/pip install "psycopg[binary]" dj-database-url pytest pytest-django
```

- [ ] **Step 2: Split requirements**

Create `requirements-local-llm.txt` with exactly:

```
# Only needed to run the translation model on this machine.
# Not installed in containers: inference runs on the GPU host over OLLAMA_URL.
llama_cpp_python==0.3.23
huggingface_hub==1.17.0
hf-xet==1.5.0
```

In `requirements.txt`, delete the three lines `llama_cpp_python==0.3.23`, `huggingface_hub==1.17.0`, and `hf-xet==1.5.0`, then append:

```
psycopg[binary]==3.2.10
dj-database-url==3.0.1
pytest==8.4.2
pytest-django==4.11.1
```

Pin whatever `./venv/bin/pip freeze | grep -iE '^(psycopg|dj-database-url|pytest|pytest-django)='` actually reports rather than trusting the numbers above.

- [ ] **Step 3: Write the failing test**

Create `tests_settings/__init__.py` (empty) and `tests_settings/test_settings_env.py`:

```python
import os
import importlib
from django.conf import settings


def test_two_database_aliases_exist():
    assert "default" in settings.DATABASES
    assert "direct" in settings.DATABASES


def test_direct_alias_disables_pooling_behaviour():
    assert settings.DATABASES["direct"]["CONN_MAX_AGE"] == 0


def test_secret_key_comes_from_env(monkeypatch):
    monkeypatch.setenv("DJANGO_SECRET_KEY", "from-the-environment")
    import beynunehcheh.settings as s
    importlib.reload(s)
    assert s.SECRET_KEY == "from-the-environment"


def test_debug_defaults_off_when_env_absent(monkeypatch):
    monkeypatch.delenv("DJANGO_DEBUG", raising=False)
    import beynunehcheh.settings as s
    importlib.reload(s)
    assert s.DEBUG is False
```

- [ ] **Step 4: Create pytest config**

Create `pytest.ini`:

```ini
[pytest]
DJANGO_SETTINGS_MODULE = beynunehcheh.settings
python_files = test_*.py
testpaths = tests_settings search gazette ibay core
addopts = -q --reuse-db
```

- [ ] **Step 5: Run test to verify it fails**

Run: `./venv/bin/pytest tests_settings/test_settings_env.py -v`
Expected: FAIL — `KeyError: 'direct'` on the first test, and `test_debug_defaults_off_when_env_absent` fails because `DEBUG` is hardcoded `True`.

- [ ] **Step 6: Make settings env-driven**

In `beynunehcheh/settings.py`, replace lines 28-33 (the `SECRET_KEY`, `DEBUG` and `ALLOWED_HOSTS` block) with:

```python
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure--xa13!_4x+q4gzeqct-hi9(9mukffbr5&acj36uj)s$oyz8kav",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get(
        "DJANGO_ALLOWED_HOSTS", "10.0.0.107,localhost,127.0.0.1"
    ).split(",")
    if h.strip()
]
```

The insecure key stays as a development fallback only; `compose.prod.yml` already declares `DJANGO_SECRET_KEY` with `:?` so production cannot start without a real one.

- [ ] **Step 7: Add the two database aliases**

Replace the `DATABASES` block (lines 84-89) with:

```python
import dj_database_url

_default_url = os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
_direct_url = os.environ.get("DATABASE_URL_DIRECT", _default_url)

DATABASES = {
    # Pooled in production (PgBouncer, transaction mode). Short web requests.
    "default": dj_database_url.parse(
        _default_url,
        conn_max_age=int(os.environ.get("DJANGO_CONN_MAX_AGE", "0")),
        disable_server_side_cursors=True,
    ),
    # Never pooled. Management commands stream over this alias and need real
    # server-side cursors; DDL cannot run through a transaction-mode pool.
    "direct": dj_database_url.parse(
        _direct_url,
        conn_max_age=0,
        disable_server_side_cursors=False,
    ),
}
```

- [ ] **Step 8: Register the postgres contrib app**

In `INSTALLED_APPS` (line 38), add `'django.contrib.postgres',` immediately after `'django.contrib.staticfiles',`. `SearchVectorField` and the GIN index classes live there.

- [ ] **Step 9: Run test to verify it passes**

Run: `./venv/bin/pytest tests_settings/test_settings_env.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 10: Simplify the Dockerfile now that requirements are split**

In `docker/api.Dockerfile`, replace the whole `RUN grep -viE ...` block with:

```dockerfile
RUN pip install -r requirements.txt \
    && pip install \
        django-ninja \
        gunicorn \
        uvicorn \
        anthropic \
        python-docx
```

Delete the comment paragraph above it that explains the filter, since the filter is gone.

- [ ] **Step 11: Verify the image still builds and compose still validates**

```bash
docker compose -f compose.yml build api
docker compose -f compose.yml --profile web config --quiet && echo "dev OK"
```
Expected: build succeeds, `dev OK`.

- [ ] **Step 12: Commit**

```bash
jj commit -m "build: env-driven settings, dual DB aliases, pytest, requirements split"
```

---

### Task 2: Move the translation client into a `core` app

**Files:**
- Create: `core/__init__.py`, `core/apps.py`, `core/models.py`, `core/translate.py`, `core/migrations/__init__.py`, `core/migrations/0001_initial.py`, `core/migrations/0002_copy_translation_cache.py`, `core/tests/__init__.py`, `core/tests/test_translation_cache.py`
- Modify: `beynunehcheh/settings.py` (INSTALLED_APPS), `gazette/translate.py`, `gazette/models.py`, `gazette/sync_service.py:15`, `gazette/management/commands/retranslate_gazette.py:6`
- Test: `core/tests/test_translation_cache.py`

**Interfaces:**
- Consumes: Task 1's settings.
- Produces: `core.models.TranslationCache(source_hash, translated_text)`; `core.translate.translate_auto`, `translate_dv_to_en`, `translate_en_to_dv`, `translate_auto_sync`, `is_dhivehi`, `sentence_boundary` — same signatures as the current `gazette.translate`.

- [ ] **Step 1: Scaffold the app**

```bash
cd /home/flame/random/beynunehcheh
./venv/bin/python manage.py startapp core
rm core/views.py core/admin.py core/tests.py
mkdir -p core/tests && touch core/tests/__init__.py
```

Add `'core',` to `INSTALLED_APPS` in `beynunehcheh/settings.py`, immediately before `'gazette',`.

- [ ] **Step 2: Write the failing test**

Create `core/tests/test_translation_cache.py`:

```python
import pytest
from core.models import TranslationCache
from core import translate


@pytest.mark.django_db
def test_cache_round_trips():
    TranslationCache.objects.create(source_hash="abc", translated_text="hello")
    assert TranslationCache.objects.get(source_hash="abc").translated_text == "hello"


def test_is_dhivehi_detects_thaana():
    assert translate.is_dhivehi("ވަޒީފާގެ ފުރުޞަތު") is True
    assert translate.is_dhivehi("Job Opportunity") is False


def test_sentence_boundary_returns_full_length_for_short_text():
    assert translate.sentence_boundary("short") == len("short")


def test_gazette_shim_still_exports_the_same_callables():
    from gazette import translate as legacy
    assert legacy.translate_auto is translate.translate_auto
    assert legacy.is_dhivehi is translate.is_dhivehi
```

- [ ] **Step 3: Run test to verify it fails**

Run: `./venv/bin/pytest core/tests/test_translation_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.translate'`.

- [ ] **Step 4: Move the model**

Create `core/models.py`:

```python
from django.db import models


class TranslationCache(models.Model):
    source_hash = models.CharField(max_length=64, unique=True)
    translated_text = models.TextField()

    class Meta:
        verbose_name_plural = "translation cache entries"

    def __str__(self):
        return self.source_hash[:12]
```

Delete the `TranslationCache` class from `gazette/models.py` (the first class in the file).

- [ ] **Step 5: Move the translation client**

```bash
git mv gazette/translate.py core/translate.py 2>/dev/null || mv gazette/translate.py core/translate.py
```

In `core/translate.py`, change the two deferred imports inside `_cached_translation` and `_cache_translation` from `from gazette.models import TranslationCache` to `from core.models import TranslationCache`.

- [ ] **Step 6: Leave a shim so existing callers keep working**

Create `gazette/translate.py`:

```python
"""Backwards-compatible shim. The translation client now lives in `core`,
because enrichment and search need it too -- see spec section 3."""

from core.translate import (  # noqa: F401
    is_dhivehi,
    sentence_boundary,
    translate_auto,
    translate_auto_sync,
    translate_dv_to_en,
    translate_dv_to_en_sync,
    translate_en_to_dv,
    translate_en_to_dv_sync,
)
```

- [ ] **Step 7: Point the real callers at `core` directly**

In `gazette/sync_service.py` line 15, change:

```python
from gazette.translate import translate_auto, sentence_boundary
```

to:

```python
from core.translate import translate_auto, sentence_boundary
```

In `gazette/management/commands/retranslate_gazette.py` line 6, change `from gazette.translate import` to `from core.translate import`.

- [ ] **Step 8: Create the migrations**

```bash
./venv/bin/python manage.py makemigrations core gazette
```

This produces `core/migrations/0001_initial.py` (creates the table) and a `gazette` migration deleting `TranslationCache`.

- [ ] **Step 9: Write the data-copy migration**

Create `core/migrations/0002_copy_translation_cache.py`:

```python
from django.db import migrations


def copy_forward(apps, schema_editor):
    """Copy rows from gazette.TranslationCache before the gazette migration
    drops that table. 676 rows at time of writing -- small enough to do in
    one pass."""
    Old = apps.get_model("gazette", "TranslationCache")
    New = apps.get_model("core", "TranslationCache")
    New.objects.bulk_create(
        [
            New(source_hash=row.source_hash, translated_text=row.translated_text)
            for row in Old.objects.all().iterator(chunk_size=500)
        ],
        batch_size=500,
        ignore_conflicts=True,
    )


def copy_back(apps, schema_editor):
    Old = apps.get_model("gazette", "TranslationCache")
    New = apps.get_model("core", "TranslationCache")
    Old.objects.bulk_create(
        [
            Old(source_hash=row.source_hash, translated_text=row.translated_text)
            for row in New.objects.all().iterator(chunk_size=500)
        ],
        batch_size=500,
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
        ("gazette", "0005_translationcache"),
    ]
    operations = [migrations.RunPython(copy_forward, copy_back)]
```

- [ ] **Step 10: Order the gazette deletion after the copy**

Open the generated `gazette` migration that deletes `TranslationCache` and add `("core", "0002_copy_translation_cache")` to its `dependencies` list. Without this, the table can be dropped before the rows are copied.

- [ ] **Step 11: Run the migrations and the test**

```bash
./venv/bin/python manage.py migrate
./venv/bin/pytest core/tests/test_translation_cache.py -v
```
Expected: migrations apply cleanly; 4 tests PASS.

- [ ] **Step 12: Verify the 676 rows survived**

```bash
./venv/bin/python manage.py shell -c "from core.models import TranslationCache; print(TranslationCache.objects.count())"
```
Expected: `676` (or whatever `gazette_translationcache` held before the move — check first if unsure).

- [ ] **Step 13: Commit**

```bash
jj commit -m "refactor: move translation client and cache into core app"
```

---

### Task 3: Migrate to PostgreSQL

**Files:**
- Create: `core/migrations/0003_postgres_extensions.py`
- Modify: nothing else
- Test: `tests_settings/test_postgres.py`

**Interfaces:**
- Consumes: Task 1's `DATABASES`, Task 2's migrated apps.
- Produces: a working Postgres database containing all existing rows, with `pg_trgm` and `unaccent` enabled.

- [ ] **Step 1: Start Postgres**

```bash
cd /home/flame/random/beynunehcheh
docker compose up -d db
docker compose exec db pg_isready -U beynunehcheh
```
Expected: `accepting connections`.

- [ ] **Step 2: Write the failing test**

Create `tests_settings/test_postgres.py`:

```python
import pytest
from django.db import connection


@pytest.mark.django_db
def test_running_on_postgres():
    assert connection.vendor == "postgresql"


@pytest.mark.django_db
def test_required_extensions_are_installed():
    with connection.cursor() as cur:
        cur.execute("SELECT extname FROM pg_extension")
        installed = {row[0] for row in cur.fetchall()}
    assert "pg_trgm" in installed
    assert "unaccent" in installed
```

- [ ] **Step 3: Run test to verify it fails**

Run: `DATABASE_URL=postgres://beynunehcheh:beynunehcheh@localhost:5432/beynunehcheh ./venv/bin/pytest tests_settings/test_postgres.py -v`
Expected: FAIL — `test_required_extensions_are_installed` fails because neither extension exists.

- [ ] **Step 4: Add the extensions migration**

Create `core/migrations/0003_postgres_extensions.py`:

```python
from django.contrib.postgres.operations import TrigramExtension, UnaccentExtension
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("core", "0002_copy_translation_cache")]
    operations = [TrigramExtension(), UnaccentExtension()]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `DATABASE_URL=postgres://beynunehcheh:beynunehcheh@localhost:5432/beynunehcheh ./venv/bin/pytest tests_settings/test_postgres.py -v`
Expected: PASS, 2 tests.

- [ ] **Step 6: Dump the SQLite data**

```bash
./venv/bin/python manage.py dumpdata \
  --natural-foreign --natural-primary \
  --exclude contenttypes --exclude auth.permission --exclude admin.logentry \
  --indent 0 -o /tmp/beynunehcheh_dump.json
ls -lh /tmp/beynunehcheh_dump.json
```

- [ ] **Step 7: Record the source row counts**

```bash
./venv/bin/python manage.py shell -c "
from ibay.models import Product, Seller, Category, ProductImage, ProductInfo
from gazette.models import Iulaan, Office, IulaanType
from core.models import TranslationCache
for m in (Product, Seller, Category, ProductImage, ProductInfo, Iulaan, Office, IulaanType, TranslationCache):
    print(m.__name__, m.objects.count())
" | tee /tmp/counts_sqlite.txt
```

Expected to include `Product 20445`, `ProductImage 34895`, `Iulaan 306`, `Office 170`.

- [ ] **Step 8: Migrate and load into Postgres**

```bash
export DATABASE_URL=postgres://beynunehcheh:beynunehcheh@localhost:5432/beynunehcheh
./venv/bin/python manage.py migrate
./venv/bin/python manage.py loaddata /tmp/beynunehcheh_dump.json
```

If `loaddata` fails on treebeard `Category` paths, load it separately after the rest: `dumpdata ibay.Category` on its own and load that file first, since `MP_Node` rows are order-sensitive.

- [ ] **Step 9: Verify the counts match**

```bash
export DATABASE_URL=postgres://beynunehcheh:beynunehcheh@localhost:5432/beynunehcheh
./venv/bin/python manage.py shell -c "
from ibay.models import Product, Seller, Category, ProductImage, ProductInfo
from gazette.models import Iulaan, Office, IulaanType
from core.models import TranslationCache
for m in (Product, Seller, Category, ProductImage, ProductInfo, Iulaan, Office, IulaanType, TranslationCache):
    print(m.__name__, m.objects.count())
" | tee /tmp/counts_postgres.txt
diff /tmp/counts_sqlite.txt /tmp/counts_postgres.txt && echo "COUNTS MATCH"
```
Expected: `COUNTS MATCH`.

- [ ] **Step 10: Record the working DATABASE_URL**

Append to `.env`:

```
DATABASE_URL=postgres://beynunehcheh:beynunehcheh@localhost:5432/beynunehcheh
```

- [ ] **Step 11: Commit**

```bash
jj commit -m "feat: migrate to postgresql with pg_trgm and unaccent"
```

---

### Task 4: The `Source` registry

**Files:**
- Create: `search/__init__.py`, `search/apps.py`, `search/models.py`, `search/migrations/__init__.py`, `search/migrations/0001_initial.py`, `search/migrations/0003_seed_sources.py`, `search/tests/__init__.py`, `search/tests/test_source.py`, `search/admin.py`
- Modify: `beynunehcheh/settings.py` (INSTALLED_APPS)
- Test: `search/tests/test_source.py`

**Interfaces:**
- Consumes: Task 3's Postgres.
- Produces: `search.models.Source` with fields `key`, `label_en`, `label_dv`, `site_url`, `icon`, `icon_fallback_text`, `accent`, `is_active`; seeded rows with `key='ibay'` and `key='gazette'`.

- [ ] **Step 1: Scaffold the app**

```bash
cd /home/flame/random/beynunehcheh
./venv/bin/python manage.py startapp search
rm search/views.py search/tests.py
mkdir -p search/tests search/adapters search/management/commands
touch search/tests/__init__.py search/adapters/__init__.py
touch search/management/__init__.py search/management/commands/__init__.py
```

Add `'search',` to `INSTALLED_APPS` after `'ibay',`.

- [ ] **Step 2: Write the failing test**

Create `search/tests/test_source.py`:

```python
import pytest
from search.models import Source


@pytest.mark.django_db
def test_seeded_sources_exist():
    assert Source.objects.filter(key="ibay").exists()
    assert Source.objects.filter(key="gazette").exists()


@pytest.mark.django_db
def test_source_key_is_unique():
    from django.db import IntegrityError
    with pytest.raises(IntegrityError):
        Source.objects.create(key="ibay", label_en="Dupe", site_url="https://x.mv")


@pytest.mark.django_db
def test_source_carries_bilingual_labels_and_an_icon():
    gazette = Source.objects.get(key="gazette")
    assert gazette.label_en
    assert gazette.label_dv
    assert gazette.icon.startswith("/static/sources/")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_source.py -v`
Expected: FAIL with `ImportError: cannot import name 'Source'`.

- [ ] **Step 4: Write the model**

Create `search/models.py`:

```python
from django.db import models


class Source(models.Model):
    """Provenance registry. Display metadata lives here so adding a source is
    an admin row plus an icon file; the adapter stays in code. Spec 4.3.3."""

    key = models.CharField(max_length=32, unique=True)
    label_en = models.CharField(max_length=64)
    label_dv = models.CharField(max_length=64, blank=True)
    site_url = models.URLField()
    icon = models.CharField(max_length=128, blank=True)
    icon_fallback_text = models.CharField(max_length=4, blank=True)
    accent = models.CharField(max_length=9, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return self.label_en or self.key
```

- [ ] **Step 5: Create and apply the migration**

```bash
./venv/bin/python manage.py makemigrations search
./venv/bin/python manage.py migrate search
```

- [ ] **Step 6: Write the seed migration**

Create `search/migrations/0003_seed_sources.py` (numbered 0003 so Task 5's `0002_searchdocument` slots in ahead of it):

```python
from django.db import migrations

SOURCES = [
    dict(
        key="ibay",
        label_en="iBay",
        label_dv="އައިބޭ",
        site_url="https://ibay.com.mv",
        icon="/static/sources/ibay.svg",
        icon_fallback_text="iB",
        accent="#1f6feb",
    ),
    dict(
        key="gazette",
        label_en="Gazette",
        label_dv="ގެޒެޓް",
        site_url="https://gazette.gov.mv",
        icon="/static/sources/gazette.svg",
        icon_fallback_text="ގ",
        accent="#0f766e",
    ),
]


def seed(apps, schema_editor):
    Source = apps.get_model("search", "Source")
    for row in SOURCES:
        Source.objects.update_or_create(key=row["key"], defaults=row)


def unseed(apps, schema_editor):
    Source = apps.get_model("search", "Source")
    Source.objects.filter(key__in=[r["key"] for r in SOURCES]).delete()


class Migration(migrations.Migration):
    dependencies = [("search", "0002_searchdocument")]
    operations = [migrations.RunPython(seed, unseed)]
```

Note this migration depends on `0002_searchdocument`, which Task 5 creates. Write the file now but expect `migrate` to fail until Task 5 lands — that is why Step 7 below only runs the model test.

- [ ] **Step 7: Temporarily point the seed at 0001 so this task is testable alone**

Change the `dependencies` line to `[("search", "0001_initial")]`, run:

```bash
./venv/bin/python manage.py migrate search
./venv/bin/pytest search/tests/test_source.py -v
```
Expected: PASS, 3 tests. Task 5 Step 9 restores the dependency to `0002_searchdocument`.

- [ ] **Step 8: Register in the admin**

Create `search/admin.py`:

```python
from django.contrib import admin
from search.models import Source


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("key", "label_en", "label_dv", "is_active")
    list_editable = ("is_active",)
```

- [ ] **Step 9: Add placeholder icons so the paths are not lies**

```bash
mkdir -p static/sources
cat > static/sources/gazette.svg <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" role="img" aria-label="Gazette">
  <rect width="32" height="32" rx="6" fill="#0f766e"/>
  <path d="M8 9h16v2.5H8zM8 14.5h16V17H8zM8 20h10v2.5H8z" fill="#fff"/>
</svg>
SVG
cat > static/sources/ibay.svg <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" role="img" aria-label="iBay">
  <rect width="32" height="32" rx="6" fill="#1f6feb"/>
  <path d="M10 11h3v11h-3zM17 11h5a4 4 0 010 8h-5z" fill="#fff"/>
</svg>
SVG
```

Replace these with the real site favicons later; the spec requires self-hosted assets, not hotlinks.

- [ ] **Step 10: Commit**

```bash
jj commit -m "feat(search): source provenance registry with seeded ibay and gazette"
```

---

### Task 5: The partitioned `SearchDocument` table

**Files:**
- Create: `search/migrations/0002_searchdocument.py`, `search/tests/test_searchdocument.py`
- Modify: `search/models.py`, `search/migrations/0003_seed_sources.py` (restore dependency)
- Test: `search/tests/test_searchdocument.py`

**Interfaces:**
- Consumes: Task 4's `Source`.
- Produces: `search.models.SearchDocument`, LIST-partitioned by `source`, unique on `(source, source_key)`. Django manages the model state; the table itself is created by raw SQL.

- [ ] **Step 1: Write the failing test**

Create `search/tests/test_searchdocument.py`:

```python
import pytest
from django.db import IntegrityError, connection
from search.models import SearchDocument


def _make(**kw):
    defaults = dict(
        source="gazette",
        source_key="407890",
        doc_type="news",
        url="https://gazette.gov.mv/iulaan/407890",
        title_en="Test notice",
    )
    defaults.update(kw)
    return SearchDocument.objects.create(**defaults)


@pytest.mark.django_db
def test_can_create_and_read_back():
    doc = _make()
    assert SearchDocument.objects.get(pk=doc.pk).title_en == "Test notice"


@pytest.mark.django_db
def test_source_and_source_key_are_unique_together():
    _make()
    with pytest.raises(IntegrityError):
        _make()


@pytest.mark.django_db
def test_same_source_key_allowed_under_a_different_source():
    _make(source="gazette", source_key="1")
    _make(source="ibay", source_key="1")
    assert SearchDocument.objects.filter(source_key="1").count() == 2


@pytest.mark.django_db
def test_doc_type_is_mutable_and_stays_in_the_same_partition():
    """Reclassification (spec 3.2) must be a plain UPDATE. If doc_type were the
    partition key this would migrate the row between partitions."""
    doc = _make(doc_type="news")
    doc.doc_type = "job"
    doc.save(update_fields=["doc_type"])
    assert SearchDocument.objects.get(pk=doc.pk).doc_type == "job"


@pytest.mark.django_db
def test_table_is_partitioned_by_source():
    with connection.cursor() as cur:
        cur.execute("""
            SELECT pg_get_partkeydef('search_searchdocument'::regclass)
        """)
        assert cur.fetchone()[0] == "LIST (source)"


@pytest.mark.django_db
def test_partitions_exist_for_both_sources():
    with connection.cursor() as cur:
        cur.execute("""
            SELECT c.relname FROM pg_inherits i
            JOIN pg_class c ON c.oid = i.inhrelid
            WHERE i.inhparent = 'search_searchdocument'::regclass
            ORDER BY c.relname
        """)
        names = [r[0] for r in cur.fetchall()]
    assert "search_searchdocument_gazette" in names
    assert "search_searchdocument_ibay" in names
    assert "search_searchdocument_default" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_searchdocument.py -v`
Expected: FAIL with `ImportError: cannot import name 'SearchDocument'`.

- [ ] **Step 3: Add the model**

Append to `search/models.py`:

```python
from django.contrib.postgres.search import SearchVectorField


class SearchDocument(models.Model):
    """One row per searchable entity, fully denormalized. Spec 4.1.

    The table is LIST-partitioned by `source` and created by raw SQL in
    migration 0002; Django only tracks its state. `doc_type` is deliberately
    mutable and is NOT part of any unique constraint -- reclassification
    (spec 3.2) must be an in-place UPDATE.
    """

    # identity
    source = models.CharField(max_length=32)
    source_key = models.CharField(max_length=128)
    doc_type = models.CharField(max_length=32)
    url = models.URLField(max_length=512)

    # display, language-parallel
    title_en = models.CharField(max_length=512, blank=True)
    title_dv = models.CharField(max_length=512, blank=True)
    title_latin = models.CharField(max_length=512, blank=True)
    summary_en = models.TextField(blank=True)
    summary_dv = models.TextField(blank=True)

    # search vectors. Body text is never stored here -- spec 12.1.
    vector_en = SearchVectorField(null=True)
    vector_dv = SearchVectorField(null=True)
    vector_latin = SearchVectorField(null=True)

    # universal facets
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=8, default="MVR")
    location = models.CharField(max_length=128, blank=True)
    island = models.CharField(max_length=128, blank=True)
    atoll = models.CharField(max_length=64, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    # type-specific data
    attrs = models.JSONField(default=dict, blank=True)
    card = models.JSONField(default=dict, blank=True)
    card_version = models.IntegerField(default=1)
    thumbnails = models.JSONField(default=list, blank=True)

    # ranking and bookkeeping
    quality = models.FloatField(default=0.0)
    content_hash = models.CharField(max_length=64, blank=True)
    indexed_at = models.DateTimeField(auto_now=True)
    stale_marked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "search_searchdocument"
        constraints = [
            models.UniqueConstraint(
                fields=["source", "source_key"], name="uniq_source_source_key"
            )
        ]

    def __str__(self):
        return f"{self.source}:{self.source_key}"
```

- [ ] **Step 4: Generate the state migration, then neutralise its SQL**

```bash
./venv/bin/python manage.py makemigrations search --name searchdocument
```

This creates `search/migrations/0002_searchdocument.py`. Django cannot create partitioned tables, so the generated operations must move inside `SeparateDatabaseAndState`.

- [ ] **Step 5: Rewrite the migration**

Replace the whole body of `search/migrations/0002_searchdocument.py` with the following. Keep the `CreateModel` and `AddConstraint` operations exactly as Django generated them — copy them into `state_operations` verbatim rather than retyping from memory.

```python
from django.db import migrations

CREATE = r"""
CREATE TABLE search_searchdocument (
    id              bigint GENERATED BY DEFAULT AS IDENTITY,
    source          varchar(32)  NOT NULL,
    source_key      varchar(128) NOT NULL,
    doc_type        varchar(32)  NOT NULL,
    url             varchar(512) NOT NULL,
    title_en        varchar(512) NOT NULL DEFAULT '',
    title_dv        varchar(512) NOT NULL DEFAULT '',
    title_latin     varchar(512) NOT NULL DEFAULT '',
    summary_en      text         NOT NULL DEFAULT '',
    summary_dv      text         NOT NULL DEFAULT '',
    vector_en       tsvector,
    vector_dv       tsvector,
    vector_latin    tsvector,
    price           numeric(12,2),
    currency        varchar(8)   NOT NULL DEFAULT 'MVR',
    location        varchar(128) NOT NULL DEFAULT '',
    island          varchar(128) NOT NULL DEFAULT '',
    atoll           varchar(64)  NOT NULL DEFAULT '',
    published_at    timestamptz,
    expires_at      timestamptz,
    is_active       boolean      NOT NULL DEFAULT true,
    attrs           jsonb        NOT NULL DEFAULT '{}'::jsonb,
    card            jsonb        NOT NULL DEFAULT '{}'::jsonb,
    card_version    integer      NOT NULL DEFAULT 1,
    thumbnails      jsonb        NOT NULL DEFAULT '[]'::jsonb,
    quality         double precision NOT NULL DEFAULT 0,
    content_hash    varchar(64)  NOT NULL DEFAULT '',
    indexed_at      timestamptz  NOT NULL DEFAULT now(),
    stale_marked_at timestamptz,
    -- The partition key must be in every unique constraint on a partitioned
    -- table. `source` is immutable and already part of identity, so this is
    -- free; `doc_type` is mutable and must never appear here. Spec 12.2.
    PRIMARY KEY (id, source),
    CONSTRAINT uniq_source_source_key UNIQUE (source, source_key)
) PARTITION BY LIST (source);

CREATE TABLE search_searchdocument_ibay
    PARTITION OF search_searchdocument FOR VALUES IN ('ibay');
CREATE TABLE search_searchdocument_gazette
    PARTITION OF search_searchdocument FOR VALUES IN ('gazette');
CREATE TABLE search_searchdocument_default
    PARTITION OF search_searchdocument DEFAULT;

-- All GIN indexes are partial on is_active: dead listings accumulate forever
-- and nobody searches them. Spec 12.2.
CREATE INDEX sd_vec_en_gin  ON search_searchdocument USING gin (vector_en)
    WHERE is_active;
CREATE INDEX sd_vec_dv_gin  ON search_searchdocument USING gin (vector_dv)
    WHERE is_active;
CREATE INDEX sd_vec_lat_gin ON search_searchdocument USING gin (vector_latin)
    WHERE is_active;
CREATE INDEX sd_attrs_gin   ON search_searchdocument USING gin (attrs jsonb_path_ops)
    WHERE is_active;

-- Trigram on titles only. On body text these indexes would dominate the
-- database, since they store every three-character window. Spec 12.1.
CREATE INDEX sd_title_en_trgm  ON search_searchdocument
    USING gin (title_en gin_trgm_ops) WHERE is_active;
CREATE INDEX sd_title_dv_trgm  ON search_searchdocument
    USING gin (title_dv gin_trgm_ops) WHERE is_active;
CREATE INDEX sd_title_lat_trgm ON search_searchdocument
    USING gin (title_latin gin_trgm_ops) WHERE is_active;

CREATE INDEX sd_published_brin ON search_searchdocument USING brin (published_at);
CREATE INDEX sd_type_published ON search_searchdocument (doc_type, published_at DESC)
    WHERE is_active;
CREATE INDEX sd_price          ON search_searchdocument (price) WHERE is_active;
CREATE INDEX sd_expires        ON search_searchdocument (expires_at) WHERE is_active;
CREATE INDEX sd_stale          ON search_searchdocument (stale_marked_at)
    WHERE stale_marked_at IS NOT NULL;
"""

DROP = "DROP TABLE IF EXISTS search_searchdocument CASCADE;"


class Migration(migrations.Migration):
    dependencies = [("search", "0001_initial")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(CREATE, DROP)],
            state_operations=[
                # Copy the generated operations here verbatim. They will look
                # like the following -- verify against what Django actually
                # produced rather than trusting this sketch, since a field list
                # that drifts from the model causes silent migration-state bugs:
                #
                # migrations.CreateModel(
                #     name="SearchDocument",
                #     fields=[
                #         ("id", models.BigAutoField(auto_created=True,
                #             primary_key=True, serialize=False, verbose_name="ID")),
                #         ("source", models.CharField(max_length=32)),
                #         ...every field from search/models.py...
                #     ],
                #     options={"db_table": "search_searchdocument"},
                # ),
                # migrations.AddConstraint(
                #     model_name="searchdocument",
                #     constraint=models.UniqueConstraint(
                #         fields=("source", "source_key"),
                #         name="uniq_source_source_key"),
                # ),
            ],
        )
    ]
```

The `id` field stays a plain `BigAutoField` in Django's state even though the real table uses `PRIMARY KEY (id, source)`. Django never issues DDL for this table, so the mismatch is invisible to it. One consequence to remember in later phases: a foreign key pointing at `SearchDocument` (for example `DocumentSpec` in P7, or `ClickLog` in P5) must be declared `db_constraint=False`, because Postgres cannot enforce a foreign key against a partitioned table's non-unique `id` column alone.

- [ ] **Step 6: Restore the seed migration dependency**

In `search/migrations/0003_seed_sources.py`, change `dependencies` back to `[("search", "0002_searchdocument")]`.

- [ ] **Step 7: Apply and run the tests**

```bash
./venv/bin/python manage.py migrate search
./venv/bin/pytest search/tests/test_searchdocument.py search/tests/test_source.py -v
```
Expected: PASS, 9 tests total.

- [ ] **Step 8: Verify the migration is reversible**

```bash
./venv/bin/python manage.py migrate search 0001
./venv/bin/python manage.py migrate search
```
Expected: both directions succeed with no errors.

- [ ] **Step 9: Commit**

```bash
jj commit -m "feat(search): partitioned SearchDocument table with partial GIN indexes"
```

---

### Task 6: The adapter contract and registry

**Files:**
- Create: `search/adapters/base.py`, `search/tests/test_adapter_registry.py`
- Test: `search/tests/test_adapter_registry.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `RawDocument(source: str, source_key: str, payload: dict)`
  - `DocumentDraft(source, source_key, doc_type, url, title_en, title_dv, summary_en, summary_dv, text_en, price, currency, location, island, atoll, published_at, expires_at, is_active, attrs, card, thumbnails, quality, content_hash)`
  - `SourceAdapter` protocol with `key: str`, `iter_source_keys(**filters) -> Iterator[str]`, `fetch_raw(source_key) -> RawDocument | None`, `to_document(raw) -> DocumentDraft | None`
  - `register(adapter)`, `get_adapter(key)`, `all_adapters()`

- [ ] **Step 1: Write the failing test**

Create `search/tests/test_adapter_registry.py`:

```python
import pytest
from search.adapters import base


class _Dummy:
    key = "dummy"

    def iter_source_keys(self, **filters):
        yield "1"

    def fetch_raw(self, source_key):
        if source_key != "1":
            return None
        return base.RawDocument(source="dummy", source_key="1", payload={"t": "hi"})

    def to_document(self, raw):
        return base.DocumentDraft(
            source="dummy",
            source_key=raw.source_key,
            doc_type="news",
            url="https://example.mv/1",
            title_en=raw.payload["t"],
            text_en=raw.payload["t"],
        )


def test_register_and_retrieve(monkeypatch):
    monkeypatch.setattr(base, "_REGISTRY", {})
    base.register(_Dummy())
    assert base.get_adapter("dummy").key == "dummy"
    assert [a.key for a in base.all_adapters()] == ["dummy"]


def test_unknown_adapter_raises(monkeypatch):
    monkeypatch.setattr(base, "_REGISTRY", {})
    with pytest.raises(KeyError):
        base.get_adapter("nope")


def test_duplicate_registration_raises(monkeypatch):
    monkeypatch.setattr(base, "_REGISTRY", {})
    base.register(_Dummy())
    with pytest.raises(ValueError):
        base.register(_Dummy())


def test_fetch_raw_round_trips_every_listed_key(monkeypatch):
    """Spec 3.1: a source that cannot be read back cannot be reprocessed."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    base.register(_Dummy())
    adapter = base.get_adapter("dummy")
    for key in adapter.iter_source_keys():
        assert adapter.fetch_raw(key) is not None


def test_draft_defaults_are_safe():
    d = base.DocumentDraft(
        source="s", source_key="k", doc_type="news", url="https://x.mv"
    )
    assert d.attrs == {}
    assert d.thumbnails == []
    assert d.is_active is True
    assert d.currency == "MVR"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_adapter_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'search.adapters.base'`.

- [ ] **Step 3: Write the contract**

Create `search/adapters/base.py`:

```python
"""Source adapter contract. Spec 3.1.

Every adapter implements BOTH directions. `fetch_raw` is the half that makes
reprocessing possible: without it, adding a document type later degrades from
a re-run into a re-scrape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterator, Protocol, runtime_checkable


@dataclass(slots=True)
class RawDocument:
    """Whatever the source app holds for one entity, unprocessed."""

    source: str
    source_key: str
    payload: dict[str, Any]


@dataclass(slots=True)
class DocumentDraft:
    """A source-agnostic description of one searchable entity.

    Note there is no body-text field beyond `text_en`, which is consumed to
    build a tsvector and then discarded -- SearchDocument never stores it
    (spec 12.1).
    """

    source: str
    source_key: str
    doc_type: str
    url: str

    title_en: str = ""
    title_dv: str = ""
    title_latin: str = ""
    summary_en: str = ""
    summary_dv: str = ""

    # Consumed by the indexer to build vectors, never persisted.
    text_en: str = ""
    text_dv: str = ""
    text_latin: str = ""

    price: Decimal | None = None
    currency: str = "MVR"
    location: str = ""
    island: str = ""
    atoll: str = ""
    published_at: datetime | None = None
    expires_at: datetime | None = None
    is_active: bool = True

    attrs: dict[str, Any] = field(default_factory=dict)
    card: dict[str, Any] = field(default_factory=dict)
    thumbnails: list[str] = field(default_factory=list)
    quality: float = 0.0
    content_hash: str = ""


@runtime_checkable
class SourceAdapter(Protocol):
    key: str

    def iter_source_keys(self, **filters: Any) -> Iterator[str]: ...

    def fetch_raw(self, source_key: str) -> RawDocument | None: ...

    def to_document(self, raw: RawDocument) -> DocumentDraft | None: ...


_REGISTRY: dict[str, SourceAdapter] = {}


def register(adapter: SourceAdapter) -> SourceAdapter:
    if adapter.key in _REGISTRY:
        raise ValueError(f"adapter already registered for source {adapter.key!r}")
    _REGISTRY[adapter.key] = adapter
    return adapter


def get_adapter(key: str) -> SourceAdapter:
    try:
        return _REGISTRY[key]
    except KeyError:
        raise KeyError(
            f"no adapter registered for source {key!r}; "
            f"known: {sorted(_REGISTRY)}"
        ) from None


def all_adapters() -> list[SourceAdapter]:
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest search/tests/test_adapter_registry.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
jj commit -m "feat(search): source adapter contract and registry"
```

---

### Task 7: The iBay adapter

**Files:**
- Create: `search/adapters/ibay.py`, `search/tests/test_adapter_ibay.py`
- Modify: `search/apps.py`
- Test: `search/tests/test_adapter_ibay.py`

**Interfaces:**
- Consumes: `search.adapters.base` from Task 6.
- Produces: `search.adapters.ibay.IbayAdapter` with `key = "ibay"`, registered at app-ready time.

- [ ] **Step 1: Write the failing test**

Create `search/tests/test_adapter_ibay.py`:

```python
import pytest
from decimal import Decimal
from ibay.models import Product, Seller
from search.adapters.ibay import IbayAdapter


@pytest.fixture
def product(db):
    seller = Seller.objects.create(id=1, name="Test Seller", is_premium=True)
    return Product.objects.create(
        listing_id=6436842,
        name="SG MEN Eau De Toilette 100ml",
        url="https://ibay.com.mv/index.php?page=item&id=6436842",
        seller=seller,
        price=Decimal("280.00"),
        product_location="Male City/Male",
        description="Description\n\nThe freshness of bergamot.",
        status="SCRAPED",
    )


def test_key(product):
    assert IbayAdapter().key == "ibay"


def test_iter_source_keys_yields_listing_ids(product):
    assert "6436842" in list(IbayAdapter().iter_source_keys())


def test_fetch_raw_returns_none_for_unknown_key(product):
    assert IbayAdapter().fetch_raw("999999999") is None


def test_to_document_maps_the_scraped_fields(product):
    a = IbayAdapter()
    draft = a.to_document(a.fetch_raw("6436842"))
    assert draft.source == "ibay"
    assert draft.source_key == "6436842"
    assert draft.doc_type == "shopping"
    assert draft.title_en == "SG MEN Eau De Toilette 100ml"
    assert draft.price == Decimal("280.00")
    assert draft.location == "Male City/Male"
    assert "bergamot" in draft.text_en


def test_summary_strips_the_description_boilerplate(product):
    a = IbayAdapter()
    draft = a.to_document(a.fetch_raw("6436842"))
    assert not draft.summary_en.startswith("Description")
    assert len(draft.summary_en) <= 240


def test_card_carries_the_source_key_not_an_icon_path(product):
    """Spec 4.3.3: cards store the registry key; the icon is resolved via /meta."""
    a = IbayAdapter()
    draft = a.to_document(a.fetch_raw("6436842"))
    assert draft.card["source"] == "ibay"
    assert "icon" not in draft.card


def test_error_status_products_are_inactive(product):
    product.status = "ERROR"
    product.save(update_fields=["status"])
    a = IbayAdapter()
    draft = a.to_document(a.fetch_raw("6436842"))
    assert draft.is_active is False


def test_content_hash_changes_with_the_text(product):
    a = IbayAdapter()
    first = a.to_document(a.fetch_raw("6436842")).content_hash
    product.description = "Something else entirely"
    product.save(update_fields=["description"])
    second = a.to_document(a.fetch_raw("6436842")).content_hash
    assert first and second and first != second
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_adapter_ibay.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'search.adapters.ibay'`.

- [ ] **Step 3: Write the adapter**

Create `search/adapters/ibay.py`:

```python
"""iBay adapter. P1 maps scraped fields only -- no language model is called
here. doc_type is assigned by the deterministic category prior from spec 5.3;
the LLM classifier arrives in P4."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterator

from ibay.models import Product
from search.adapters.base import DocumentDraft, RawDocument

# Spec 5.3 category priors. Property and job promotion happen here because the
# iBay category tree already carries the signal.
_CATEGORY_DOC_TYPE = {
    "Jobs": "job",
    "Housing & Real Estate": "property",
    "Announcements & Events": "news",
}
_DEFAULT_DOC_TYPE = "shopping"

_BOILERPLATE = re.compile(r"^\s*Description\s*", re.IGNORECASE)
_WS = re.compile(r"\s+")


def _summarize(text: str, limit: int = 240) -> str:
    text = _WS.sub(" ", _BOILERPLATE.sub("", text or "")).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "..."


class IbayAdapter:
    key = "ibay"

    def iter_source_keys(self, **filters: Any) -> Iterator[str]:
        qs = Product.objects.all()
        if since := filters.get("since"):
            qs = qs.filter(updated_at__gte=since)
        for listing_id in qs.values_list("listing_id", flat=True).iterator(
            chunk_size=500
        ):
            yield str(listing_id)

    def fetch_raw(self, source_key: str) -> RawDocument | None:
        try:
            product = (
                Product.objects.select_related("seller")
                .prefetch_related("images", "info", "categories")
                .get(listing_id=int(source_key))
            )
        except (Product.DoesNotExist, ValueError):
            return None
        return RawDocument(
            source=self.key,
            source_key=source_key,
            payload={
                "product": product,
                "categories": [c.name for c in product.categories.all()],
                "images": [i.image_url for i in product.images.all()],
                "info": {i.info_key: i.info_value for i in product.info.all()},
            },
        )

    def to_document(self, raw: RawDocument) -> DocumentDraft | None:
        p: Product = raw.payload["product"]
        categories: list[str] = raw.payload["categories"]
        images: list[str] = raw.payload["images"]
        info: dict[str, str] = raw.payload["info"]

        doc_type = _DEFAULT_DOC_TYPE
        for name in categories:
            if name in _CATEGORY_DOC_TYPE:
                doc_type = _CATEGORY_DOC_TYPE[name]
                break

        body = p.description or ""
        text_en = f"{p.name}\n{body}\n" + "\n".join(
            f"{k} {v}" for k, v in info.items()
        )

        return DocumentDraft(
            source=self.key,
            source_key=str(p.listing_id),
            doc_type=doc_type,
            url=p.url,
            title_en=p.name,
            summary_en=_summarize(body),
            text_en=text_en,
            price=p.price,
            currency="MVR",
            location=p.product_location or "",
            is_active=p.status != "ERROR",
            attrs={"category_path": categories, "specs_raw": info},
            card={
                "source": self.key,
                "title": p.name,
                "price_display": f"MVR {p.price:,.0f}" if p.price else None,
                "location": p.product_location or "",
                "hero_image": images[0] if images else None,
                "image_count": len(images),
                "seller_name": p.seller.name if p.seller else "",
                "seller_is_premium": bool(p.seller and p.seller.is_premium),
                "condition": info.get("Item Condition", ""),
                "brand": info.get("Brand", ""),
            },
            thumbnails=images[:5],
            quality=_quality(p, images, info),
            content_hash=hashlib.sha256(text_en.encode()).hexdigest(),
        )


def _quality(product: Product, images: list[str], info: dict[str, str]) -> float:
    """Completeness score in [0, 1]. Feeds ranking (spec 7)."""
    score = 0.0
    score += 0.3 if product.description else 0.0
    score += 0.3 if images else 0.0
    score += 0.2 if product.price is not None else 0.0
    score += 0.2 if info else 0.0
    return round(score, 3)
```

- [ ] **Step 4: Register the adapter at app-ready time**

Replace `search/apps.py` with:

```python
from django.apps import AppConfig


class SearchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "search"

    def ready(self):
        from search.adapters import base
        from search.adapters.ibay import IbayAdapter

        if "ibay" not in base._REGISTRY:
            base.register(IbayAdapter())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./venv/bin/pytest search/tests/test_adapter_ibay.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 6: Commit**

```bash
jj commit -m "feat(search): ibay adapter with category priors and quality scoring"
```

---

### Task 8: The gazette adapter

**Files:**
- Create: `search/adapters/gazette.py`, `search/tests/test_adapter_gazette.py`
- Modify: `search/apps.py`
- Test: `search/tests/test_adapter_gazette.py`

**Interfaces:**
- Consumes: `search.adapters.base` from Task 6.
- Produces: `search.adapters.gazette.GazetteAdapter` with `key = "gazette"`; module-level `IULAAN_TYPE_DOC_TYPE` mapping consumed by later phases.

- [ ] **Step 1: Write the failing test**

Create `search/tests/test_adapter_gazette.py`:

```python
import pytest
from gazette.models import Iulaan, IulaanType, Office
from search.adapters.gazette import GazetteAdapter


@pytest.fixture
def iulaan(db):
    office = Office.objects.create(name="މިނިސްޓްރީ", translated_name="Ministry")
    jobs = IulaanType.objects.create(name="ވަޒީފާގެ ފުރުޞަތު")
    return Iulaan.objects.create(
        id="407890",
        title="ވަޒީފާގެ ފުރުޞަތު",
        translated_title="Job Opportunity",
        office=office,
        iulaan_type=jobs,
        additional_info={"ނަންބަރު": "674-A/2026/46"},
        attachments={"iulaan": "https://storage.googleapis.com/x/1.pdf"},
        body='<td><p dir="RTL"><strong>އަސާސީ މުސާރަ:</strong></p></td>'
             '<td><p dir="RTL">މަހަކު 10,750 ރުފިޔާ</p></td>',
        translated_body="Basic salary: 10,750 rufiyaa per month",
    )


def test_key(iulaan):
    assert GazetteAdapter().key == "gazette"


def test_iter_and_fetch_round_trip(iulaan):
    a = GazetteAdapter()
    keys = list(a.iter_source_keys())
    assert "407890" in keys
    assert a.fetch_raw("407890") is not None
    assert a.fetch_raw("does-not-exist") is None


def test_job_type_maps_to_job(iulaan):
    a = GazetteAdapter()
    assert a.to_document(a.fetch_raw("407890")).doc_type == "job"


def test_unmapped_type_falls_back_to_news(iulaan):
    """Spec 5.3: news is the default sink, there is no `unknown` type."""
    iulaan.iulaan_type = IulaanType.objects.create(name="މުބާރާތް")
    iulaan.save(update_fields=["iulaan_type"])
    a = GazetteAdapter()
    assert a.to_document(a.fetch_raw("407890")).doc_type == "news"


def test_missing_type_falls_back_to_news(iulaan):
    iulaan.iulaan_type = None
    iulaan.save(update_fields=["iulaan_type"])
    a = GazetteAdapter()
    assert a.to_document(a.fetch_raw("407890")).doc_type == "news"


def test_html_is_stripped_from_indexed_text(iulaan):
    """Spec 6.2: markup tokens must never reach the tsvector."""
    a = GazetteAdapter()
    draft = a.to_document(a.fetch_raw("407890"))
    for token in ("<td>", "dir=", "strong", "RTL"):
        assert token not in draft.text_dv
    assert "10,750" in draft.text_dv


def test_thaana_title_lands_in_the_dv_field_and_english_in_en(iulaan):
    a = GazetteAdapter()
    draft = a.to_document(a.fetch_raw("407890"))
    assert draft.title_dv == "ވަޒީފާގެ ފުރުޞަތު"
    assert draft.title_en == "Job Opportunity"


def test_url_points_at_the_original(iulaan):
    a = GazetteAdapter()
    assert a.to_document(a.fetch_raw("407890")).url == (
        "https://gazette.gov.mv/iulaan/407890"
    )


def test_card_names_the_source(iulaan):
    a = GazetteAdapter()
    assert a.to_document(a.fetch_raw("407890")).card["source"] == "gazette"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_adapter_gazette.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'search.adapters.gazette'`.

- [ ] **Step 3: Write the adapter**

Create `search/adapters/gazette.py`:

```python
"""Gazette adapter. P1 maps scraped fields only.

Bodies are raw HTML (spec 5.6), so markup is stripped before it can reach a
tsvector. The salary-table parsing that exploits the table structure arrives
in P3 alongside attachments.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterator

from lxml import html as lxml_html

from gazette.models import Iulaan
from search.adapters.base import DocumentDraft, RawDocument

# Spec 5.3 classification priors. Anything absent from this table becomes
# news -- there is deliberately no `unknown` bucket.
IULAAN_TYPE_DOC_TYPE = {
    "ވަޒީފާގެ ފުރުޞަތު": "job",
    "Job Opportunity": "job",
    "ކުއްޔަށް ދިނުން": "property",
    "ކުއްޔަށް ހިފުން": "property",
}
_DEFAULT_DOC_TYPE = "news"

_WS = re.compile(r"\s+")


def strip_html(raw: str) -> str:
    """Return visible text only. Gazette bodies are Word-exported HTML tables;
    indexing `td`/`valign`/`strong` as lexemes would poison the vocabulary."""
    if not raw or not raw.strip():
        return ""
    try:
        text = lxml_html.fromstring(raw).text_content()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", raw)
    return _WS.sub(" ", text).strip()


def _summarize(text: str, limit: int = 240) -> str:
    text = _WS.sub(" ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "..."


class GazetteAdapter:
    key = "gazette"

    def iter_source_keys(self, **filters: Any) -> Iterator[str]:
        qs = Iulaan.objects.all()
        if doc_ids := filters.get("ids"):
            qs = qs.filter(id__in=doc_ids)
        for pk in qs.values_list("id", flat=True).iterator(chunk_size=500):
            yield str(pk)

    def fetch_raw(self, source_key: str) -> RawDocument | None:
        try:
            iulaan = Iulaan.objects.select_related("office", "iulaan_type").get(
                id=source_key
            )
        except Iulaan.DoesNotExist:
            return None
        return RawDocument(
            source=self.key,
            source_key=source_key,
            payload={"iulaan": iulaan},
        )

    def to_document(self, raw: RawDocument) -> DocumentDraft | None:
        i: Iulaan = raw.payload["iulaan"]

        type_name = i.iulaan_type.name if i.iulaan_type else ""
        doc_type = IULAAN_TYPE_DOC_TYPE.get(type_name, _DEFAULT_DOC_TYPE)

        body_dv = strip_html(i.body)
        body_en = _WS.sub(" ", i.translated_body or "").strip()
        office_en = (i.office.translated_name or i.office.name) if i.office else ""
        office_dv = i.office.name if i.office else ""

        text_dv = f"{i.title} {office_dv} {type_name} {body_dv}".strip()
        text_en = f"{i.translated_title} {office_en} {body_en}".strip()

        return DocumentDraft(
            source=self.key,
            source_key=str(i.id),
            doc_type=doc_type,
            url=i.url,
            title_dv=i.title or "",
            title_en=i.translated_title or "",
            summary_dv=_summarize(body_dv),
            summary_en=_summarize(body_en),
            text_dv=text_dv,
            text_en=text_en,
            attrs={
                "office": office_en,
                "office_dv": office_dv,
                "announcement_type": type_name,
                "additional_info": i.additional_info or {},
                "attachment_count": len(i.attachments or {}),
            },
            card={
                "source": self.key,
                "title": i.translated_title or i.title,
                "office": office_en,
                "announcement_type": type_name,
                "external_url": i.url,
                "attachment_count": len(i.attachments or {}),
            },
            quality=_quality(body_dv, i),
            content_hash=hashlib.sha256(
                f"{i.title}{i.body}".encode()
            ).hexdigest(),
        )


def _quality(body_dv: str, iulaan: Iulaan) -> float:
    score = 0.0
    score += 0.4 if len(body_dv) >= 500 else 0.1
    score += 0.2 if iulaan.translated_title else 0.0
    score += 0.2 if iulaan.office_id else 0.0
    score += 0.2 if iulaan.attachments else 0.0
    return round(score, 3)
```

- [ ] **Step 4: Register it**

In `search/apps.py`, extend `ready()`:

```python
    def ready(self):
        from search.adapters import base
        from search.adapters.gazette import GazetteAdapter
        from search.adapters.ibay import IbayAdapter

        for adapter in (IbayAdapter(), GazetteAdapter()):
            if adapter.key not in base._REGISTRY:
                base.register(adapter)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./venv/bin/pytest search/tests/test_adapter_gazette.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 6: Commit**

```bash
jj commit -m "feat(search): gazette adapter with html stripping and news fallback"
```

---

### Task 9: Indexing and the reindex command

**Files:**
- Create: `search/indexing.py`, `search/management/commands/reindex.py`, `search/tests/test_indexing.py`, `search/tests/test_reindex_command.py`
- Test: `search/tests/test_indexing.py`, `search/tests/test_reindex_command.py`

**Interfaces:**
- Consumes: `DocumentDraft` (Task 6), both adapters (Tasks 7-8), `SearchDocument` (Task 5).
- Produces: `search.indexing.upsert_drafts(drafts) -> int`, `search.indexing.reindex_source(key, *, limit=None, only_stale=False, batch_size=500) -> int`.

- [ ] **Step 1: Write the failing test for the indexer**

Create `search/tests/test_indexing.py`:

```python
import pytest
from decimal import Decimal
from django.db import connection
from search.adapters.base import DocumentDraft
from search.indexing import upsert_drafts
from search.models import SearchDocument


def _draft(**kw):
    d = dict(
        source="gazette",
        source_key="1",
        doc_type="news",
        url="https://gazette.gov.mv/iulaan/1",
        title_en="Water supply notice",
        text_en="The ministry announces a water supply interruption",
    )
    d.update(kw)
    return DocumentDraft(**d)


@pytest.mark.django_db
def test_insert_creates_a_row():
    assert upsert_drafts([_draft()]) == 1
    assert SearchDocument.objects.count() == 1


@pytest.mark.django_db
def test_reinsert_updates_rather_than_duplicating():
    upsert_drafts([_draft()])
    upsert_drafts([_draft(title_en="Amended notice")])
    assert SearchDocument.objects.count() == 1
    assert SearchDocument.objects.get().title_en == "Amended notice"


@pytest.mark.django_db
def test_english_vector_is_populated():
    upsert_drafts([_draft()])
    doc = SearchDocument.objects.get()
    with connection.cursor() as cur:
        cur.execute(
            "SELECT vector_en IS NOT NULL AND vector_en != '' "
            "FROM search_searchdocument WHERE id = %s",
            [doc.id],
        )
        assert cur.fetchone()[0] is True


@pytest.mark.django_db
def test_body_text_is_not_persisted():
    """Spec 12.1: only vectors, never the text they were built from."""
    upsert_drafts([_draft()])
    columns = {f.name for f in SearchDocument._meta.get_fields()}
    assert "text_en" not in columns
    assert "text_dv" not in columns


@pytest.mark.django_db
def test_reindex_clears_the_stale_flag():
    from django.utils import timezone
    upsert_drafts([_draft()])
    SearchDocument.objects.update(stale_marked_at=timezone.now())
    upsert_drafts([_draft()])
    assert SearchDocument.objects.get().stale_marked_at is None


@pytest.mark.django_db
def test_price_and_facets_survive_the_round_trip():
    upsert_drafts([_draft(source="ibay", price=Decimal("280.00"), location="Male")])
    doc = SearchDocument.objects.get(source="ibay")
    assert doc.price == Decimal("280.00")
    assert doc.location == "Male"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_indexing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'search.indexing'`.

- [ ] **Step 3: Write the indexer**

Create `search/indexing.py`:

```python
"""Draft-to-row conversion and bulk upsert.

Streaming discipline (spec 12.4): the source iteration uses `.iterator()` and
rows are written in batches, so reindexing 5M documents costs the same memory
as reindexing 5,000. Nothing here calls `list()` on a queryset.
"""

from __future__ import annotations

import logging
from typing import Iterable

from django.contrib.postgres.search import SearchVector
from django.db import transaction
from django.db.models import Q

from search.adapters import base
from search.adapters.base import DocumentDraft
from search.models import SearchDocument

logger = logging.getLogger(__name__)

# Written by upsert; everything except the identity pair and `id`.
_UPDATE_FIELDS = [
    "doc_type", "url",
    "title_en", "title_dv", "title_latin",
    "summary_en", "summary_dv",
    "price", "currency", "location", "island", "atoll",
    "published_at", "expires_at", "is_active",
    "attrs", "card", "thumbnails", "quality", "content_hash",
    "stale_marked_at",
]


def _row(draft: DocumentDraft) -> SearchDocument:
    return SearchDocument(
        source=draft.source,
        source_key=draft.source_key,
        doc_type=draft.doc_type,
        url=draft.url,
        title_en=draft.title_en,
        title_dv=draft.title_dv,
        title_latin=draft.title_latin,
        summary_en=draft.summary_en,
        summary_dv=draft.summary_dv,
        price=draft.price,
        currency=draft.currency,
        location=draft.location,
        island=draft.island,
        atoll=draft.atoll,
        published_at=draft.published_at,
        expires_at=draft.expires_at,
        is_active=draft.is_active,
        attrs=draft.attrs,
        card=draft.card,
        thumbnails=draft.thumbnails,
        quality=draft.quality,
        content_hash=draft.content_hash,
        stale_marked_at=None,   # a successful pass clears the work ticket
    )


def upsert_drafts(drafts: Iterable[DocumentDraft]) -> int:
    """Insert or update rows for `drafts`, then rebuild their vectors.

    Returns the number of drafts written.
    """
    batch = [_row(d) for d in drafts]
    if not batch:
        return 0

    with transaction.atomic():
        SearchDocument.objects.bulk_create(
            batch,
            update_conflicts=True,
            unique_fields=["source", "source_key"],
            update_fields=_UPDATE_FIELDS,
            batch_size=500,
        )
        _rebuild_vectors(batch)
    return len(batch)


def _rebuild_vectors(batch: list[SearchDocument]) -> None:
    """Build tsvectors from title and summary only.

    P1 populates `vector_en`. `vector_dv` and `vector_latin` are written by P2,
    once the Dhivehi normalization pipeline exists -- writing them here with the
    wrong analysis would have to be undone.
    """
    keys = Q()
    for row in batch:
        keys |= Q(source=row.source, source_key=row.source_key)
    SearchDocument.objects.filter(keys).update(
        vector_en=(
            SearchVector("title_en", weight="A", config="english")
            + SearchVector("summary_en", weight="B", config="english")
        )
    )


def reindex_source(
    key: str,
    *,
    limit: int | None = None,
    only_stale: bool = False,
    batch_size: int = 500,
    **filters,
) -> int:
    """Stream every document from one source through its adapter."""
    adapter = base.get_adapter(key)

    if only_stale:
        stale_keys = list(
            SearchDocument.objects.filter(
                source=key, stale_marked_at__isnull=False
            ).values_list("source_key", flat=True)[: limit or 1_000_000]
        )
        source_keys: Iterable[str] = iter(stale_keys)
    else:
        source_keys = adapter.iter_source_keys(**filters)

    written = 0
    buffer: list[DocumentDraft] = []

    for source_key in source_keys:
        if limit is not None and written + len(buffer) >= limit:
            break
        raw = adapter.fetch_raw(source_key)
        if raw is None:
            logger.warning("%s: no raw document for key %s", key, source_key)
            continue
        draft = adapter.to_document(raw)
        if draft is None:
            continue
        buffer.append(draft)
        if len(buffer) >= batch_size:
            written += upsert_drafts(buffer)
            buffer.clear()

    if buffer:
        written += upsert_drafts(buffer)
    return written
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest search/tests/test_indexing.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Write the rebuildability test**

This is the load-bearing test for the whole design (spec 14): everything derived must be reconstructible from the source apps alone. If it ever fails, adding a document type later silently becomes a re-scrape instead of a re-run.

Append to `search/tests/test_indexing.py`:

```python
@pytest.mark.django_db
def test_index_is_fully_rebuildable_from_source_apps():
    """Spec 3.1: SearchDocument is a disposable projection."""
    from gazette.models import Iulaan, IulaanType
    from search.indexing import reindex_source

    jobs = IulaanType.objects.create(name="ވަޒީފާގެ ފުރުޞަތު")
    for n in ("10", "11"):
        Iulaan.objects.create(
            id=n, title=f"Notice {n}", translated_title=f"Notice {n}",
            iulaan_type=jobs, additional_info={}, attachments={},
            body=f"<p>Body {n}</p>",
        )

    reindex_source("gazette")
    before = {
        (d.source, d.source_key, d.doc_type, d.title_en, d.content_hash)
        for d in SearchDocument.objects.filter(source="gazette")
    }
    assert before

    SearchDocument.objects.all().delete()
    assert SearchDocument.objects.count() == 0

    reindex_source("gazette")
    after = {
        (d.source, d.source_key, d.doc_type, d.title_en, d.content_hash)
        for d in SearchDocument.objects.filter(source="gazette")
    }
    assert after == before


@pytest.mark.django_db
def test_reclassification_is_an_in_place_update():
    """Spec 3.2 and 12.2: doc_type changes must not move a row between
    partitions or violate identity."""
    upsert_drafts([_draft(source_key="20", doc_type="news")])
    doc_id = SearchDocument.objects.get(source_key="20").id

    upsert_drafts([_draft(source_key="20", doc_type="job")])

    doc = SearchDocument.objects.get(source_key="20")
    assert doc.id == doc_id
    assert doc.doc_type == "job"
    assert SearchDocument.objects.filter(source_key="20").count() == 1
```

- [ ] **Step 6: Run the new tests**

Run: `./venv/bin/pytest search/tests/test_indexing.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 7: Write the failing test for the command**

Create `search/tests/test_reindex_command.py`:

```python
import pytest
from io import StringIO
from django.core.management import call_command
from gazette.models import Iulaan, IulaanType
from search.models import SearchDocument


@pytest.fixture
def two_iulaan(db):
    jobs = IulaanType.objects.create(name="ވަޒީފާގެ ފުރުޞަތު")
    for n in ("1", "2"):
        Iulaan.objects.create(
            id=n, title=f"Notice {n}", translated_title=f"Notice {n}",
            iulaan_type=jobs, additional_info={}, attachments={},
            body=f"<p>Body {n}</p>",
        )


@pytest.mark.django_db
def test_reindex_indexes_a_source(two_iulaan):
    out = StringIO()
    call_command("reindex", "--source", "gazette", stdout=out)
    assert SearchDocument.objects.filter(source="gazette").count() == 2
    assert "gazette" in out.getvalue()


@pytest.mark.django_db
def test_reindex_respects_limit(two_iulaan):
    call_command("reindex", "--source", "gazette", "--limit", "1", stdout=StringIO())
    assert SearchDocument.objects.filter(source="gazette").count() == 1


@pytest.mark.django_db
def test_reindex_rejects_an_unknown_source(two_iulaan):
    from django.core.management.base import CommandError
    with pytest.raises(CommandError):
        call_command("reindex", "--source", "nope", stdout=StringIO())


@pytest.mark.django_db
def test_stale_only_pass_touches_only_marked_rows(two_iulaan):
    from django.utils import timezone
    call_command("reindex", "--source", "gazette", stdout=StringIO())
    SearchDocument.objects.filter(source_key="1").update(
        stale_marked_at=timezone.now(), title_en="STALE"
    )
    call_command("reindex", "--source", "gazette", "--stale", stdout=StringIO())
    assert SearchDocument.objects.get(source_key="1").title_en == "Notice 1"
    assert SearchDocument.objects.get(source_key="1").stale_marked_at is None
```

- [ ] **Step 8: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_reindex_command.py -v`
Expected: FAIL with `Unknown command: 'reindex'`.

- [ ] **Step 9: Write the command**

Create `search/management/commands/reindex.py`:

```python
"""Stream source documents into the search index.

Runs on the `direct` database alias: streaming needs real server-side cursors,
which a transaction-mode connection pool forbids (spec 12.4).
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from search.adapters import base
from search.indexing import reindex_source


class Command(BaseCommand):
    help = "Rebuild the search index for one source or all sources."

    def add_arguments(self, parser):
        parser.add_argument("--source", help="Source key; omit for all sources.")
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument(
            "--stale",
            action="store_true",
            help="Only documents with stale_marked_at set (spec 5.7).",
        )
        parser.add_argument(
            "--database",
            default="direct",
            help="Database alias. Defaults to `direct` -- never use a pooled "
                 "alias for a streaming reindex.",
        )

    def handle(self, *args, **options):
        alias = options["database"]
        if alias not in connections:
            raise CommandError(f"unknown database alias {alias!r}")

        if options["source"]:
            try:
                base.get_adapter(options["source"])
            except KeyError as exc:
                raise CommandError(str(exc)) from None
            keys = [options["source"]]
        else:
            keys = [a.key for a in base.all_adapters()]

        total = 0
        for key in keys:
            written = reindex_source(
                key,
                limit=options["limit"],
                only_stale=options["stale"],
                batch_size=options["batch_size"],
            )
            total += written
            self.stdout.write(f"{key}: indexed {written} documents")

        self.stdout.write(self.style.SUCCESS(f"total: {total} documents"))
```

- [ ] **Step 10: Run test to verify it passes**

Run: `./venv/bin/pytest search/tests/test_reindex_command.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 11: Index the real corpus**

```bash
export DATABASE_URL=postgres://beynunehcheh:beynunehcheh@localhost:5432/beynunehcheh
time ./venv/bin/python manage.py reindex
```
Expected: roughly `ibay: indexed 20445 documents` and `gazette: indexed 306 documents`.

- [ ] **Step 12: Verify the partitions actually filled**

```bash
docker compose exec db psql -U beynunehcheh -c "
SELECT tableoid::regclass AS partition, count(*)
FROM search_searchdocument GROUP BY 1 ORDER BY 1;"
```
Expected: `search_searchdocument_gazette | 306` and `search_searchdocument_ibay | 20445`, and nothing in `_default`.

- [ ] **Step 13: Commit**

```bash
jj commit -m "feat(search): streaming reindex command with stale-only pass"
```

---

### Task 10: English lexical search

**Files:**
- Create: `search/query.py`, `search/tests/test_query.py`
- Test: `search/tests/test_query.py`

**Interfaces:**
- Consumes: `SearchDocument` (Task 5), an indexed corpus (Task 9).
- Produces: `search.query.search(q, *, doc_type=None, limit=20, candidate_limit=500) -> list[SearchResult]`, where `SearchResult` is a dataclass with `id, source, source_key, doc_type, url, title, summary, card, score`.

- [ ] **Step 1: Write the failing test**

Create `search/tests/test_query.py`:

```python
import pytest
from search.adapters.base import DocumentDraft
from search.indexing import upsert_drafts
from search import query


def _index(**kw):
    d = dict(
        source="gazette", source_key="1", doc_type="news",
        url="https://gazette.gov.mv/iulaan/1",
        title_en="Water supply interruption", summary_en="Ministry announcement",
        text_en="water supply interruption in Male",
    )
    d.update(kw)
    upsert_drafts([DocumentDraft(**d)])


@pytest.mark.django_db
def test_finds_a_document_by_title_term():
    _index()
    results = query.search("water")
    assert [r.source_key for r in results] == ["1"]


@pytest.mark.django_db
def test_returns_nothing_for_an_unmatched_term():
    _index()
    assert query.search("helicopter") == []


@pytest.mark.django_db
def test_stemming_works_via_the_english_config():
    _index()
    assert len(query.search("interruptions")) == 1


@pytest.mark.django_db
def test_title_match_outranks_summary_only_match():
    _index(source_key="1", title_en="Ferry schedule", summary_en="unrelated")
    _index(source_key="2", title_en="unrelated", summary_en="Ferry schedule")
    results = query.search("ferry")
    assert [r.source_key for r in results] == ["1", "2"]


@pytest.mark.django_db
def test_doc_type_filter_applies():
    _index(source_key="1", doc_type="news", title_en="Ferry schedule")
    _index(source_key="2", doc_type="job", title_en="Ferry captain wanted")
    assert [r.doc_type for r in query.search("ferry", doc_type="job")] == ["job"]


@pytest.mark.django_db
def test_inactive_documents_are_excluded():
    _index(is_active=False)
    assert query.search("water") == []


@pytest.mark.django_db
def test_empty_query_returns_nothing_rather_than_everything():
    _index()
    assert query.search("") == []
    assert query.search("   ") == []


@pytest.mark.django_db
def test_result_carries_the_card_payload():
    _index(card={"source": "gazette", "title": "Water supply interruption"})
    assert query.search("water")[0].card["source"] == "gazette"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_query.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'search.query'`.

- [ ] **Step 3: Write the query layer**

Create `search/query.py`:

```python
"""Lexical retrieval.

P1 is English-only: one tsquery against `vector_en`. The trilingual expansion,
trigram fallback and blended scoring described in spec 7 arrive in P2, which is
why `candidate_limit` already exists here -- the 500-row cap is what keeps
ranking and faceting independent of corpus size (spec 12.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import ExpressionWrapper, F, FloatField, Value

from search.models import SearchDocument

CANDIDATE_LIMIT = 500


@dataclass(slots=True)
class SearchResult:
    id: int
    source: str
    source_key: str
    doc_type: str
    url: str
    title: str
    summary: str
    card: dict[str, Any]
    score: float


def search(
    q: str,
    *,
    doc_type: str | None = None,
    limit: int = 20,
    candidate_limit: int = CANDIDATE_LIMIT,
) -> list[SearchResult]:
    q = (q or "").strip()
    if not q:
        return []

    tsquery = SearchQuery(q, config="english", search_type="websearch")

    qs = SearchDocument.objects.filter(is_active=True, vector_en=tsquery)
    if doc_type:
        qs = qs.filter(doc_type=doc_type)

    # A small quality nudge on top of lexical rank. The blended multi-signal
    # score in spec 7 replaces this in P2; keeping it explicit here means the
    # weights are visible rather than buried once more signals arrive.
    qs = (
        qs.annotate(
            score=ExpressionWrapper(
                SearchRank(F("vector_en"), tsquery) + F("quality") * Value(0.1),
                output_field=FloatField(),
            )
        )
        .order_by("-score", "-id")[:candidate_limit]
    )

    return [
        SearchResult(
            id=row.id,
            source=row.source,
            source_key=row.source_key,
            doc_type=row.doc_type,
            url=row.url,
            title=row.title_en or row.title_dv,
            summary=row.summary_en or row.summary_dv,
            card=row.card,
            score=float(row.score),
        )
        for row in qs[:limit]
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest search/tests/test_query.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Search the real corpus by hand**

```bash
export DATABASE_URL=postgres://beynunehcheh:beynunehcheh@localhost:5432/beynunehcheh
./venv/bin/python manage.py shell -c "
from search import query
for r in query.search('iphone', limit=5):
    print(round(r.score,4), r.doc_type, r.title[:60])
print('---')
for r in query.search('washing machine', limit=5):
    print(round(r.score,4), r.doc_type, r.title[:60])
"
```
Expected: relevant iBay listings, highest score first. This is the phase's deliverable — working English search.

- [ ] **Step 6: Run the whole suite**

Run: `./venv/bin/pytest -q`
Expected: all tests pass, no errors.

- [ ] **Step 7: Commit**

```bash
jj commit -m "feat(search): english lexical search with candidate cap"
```

---

### Task 11: The 100k load test

**Files:**
- Create: `scripts/loadtest_seed.py`, `docs/superpowers/measurements/2026-08-17-p1-load-test.md`
- Test: manual measurement, recorded to the document above

**Interfaces:**
- Consumes: everything above.
- Produces: a recorded table of `pg_relation_size` per index and p50/p95 query latency at 100,000 documents. Spec 12.7 makes this the input to any further partitioning decision.

- [ ] **Step 1: Write the seeder**

Create `scripts/loadtest_seed.py`:

```python
"""Seed synthetic documents to size the index. Spec 12.7.

Run:  ./venv/bin/python scripts/loadtest_seed.py 100000
The vocabulary is deliberately Zipf-ish rather than uniform, because GIN index
size depends heavily on vocabulary shape and uniform random words would give a
falsely reassuring answer.
"""

import os
import random
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "beynunehcheh.settings")
django.setup()

from search.adapters.base import DocumentDraft  # noqa: E402
from search.indexing import upsert_drafts  # noqa: E402

WORDS = (
    "ministry notice tender vacancy apartment iphone samsung rent island "
    "council announcement auction salary allowance office delivery brand new "
    "used furniture laptop camera boat engine service repair machine phone"
).split()
TYPES = ["shopping", "job", "news", "property"]


def phrase(rng: random.Random, n: int) -> str:
    # Zipf-weighted so common words dominate, as in real corpora.
    return " ".join(
        WORDS[min(int(rng.paretovariate(1.2)) - 1, len(WORDS) - 1)]
        for _ in range(n)
    )


def main(total: int, batch_size: int = 1000) -> None:
    rng = random.Random(20260817)
    written = 0
    while written < total:
        n = min(batch_size, total - written)
        upsert_drafts([
            DocumentDraft(
                source="ibay",
                source_key=f"synthetic-{written + i}",
                doc_type=rng.choice(TYPES),
                url=f"https://example.mv/{written + i}",
                title_en=phrase(rng, 8),
                summary_en=phrase(rng, 30),
                text_en=phrase(rng, 60),
                quality=rng.random(),
            )
            for i in range(n)
        ])
        written += n
        print(f"{written}/{total}", flush=True)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 100_000)
```

- [ ] **Step 2: Seed 100,000 documents**

```bash
export DATABASE_URL=postgres://beynunehcheh:beynunehcheh@localhost:5432/beynunehcheh
time ./venv/bin/python scripts/loadtest_seed.py 100000
```

- [ ] **Step 3: Analyze and measure index sizes**

```bash
docker compose exec db psql -U beynunehcheh -c "ANALYZE search_searchdocument;"
docker compose exec db psql -U beynunehcheh -c "
SELECT indexrelname AS index,
       pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_stat_user_indexes
WHERE relname LIKE 'search_searchdocument%'
ORDER BY pg_relation_size(indexrelid) DESC;"
docker compose exec db psql -U beynunehcheh -c "
SELECT pg_size_pretty(pg_total_relation_size('search_searchdocument')) AS total;"
```

- [ ] **Step 4: Measure query latency**

```bash
export DATABASE_URL=postgres://beynunehcheh:beynunehcheh@localhost:5432/beynunehcheh
./venv/bin/python -c "
import os, django, time, statistics
os.environ.setdefault('DJANGO_SETTINGS_MODULE','beynunehcheh.settings'); django.setup()
from search import query
terms = ['ministry','iphone notice','apartment rent','tender vacancy','island council']
lat = []
for _ in range(40):
    for t in terms:
        s = time.perf_counter(); query.search(t, limit=20); lat.append((time.perf_counter()-s)*1000)
lat.sort()
print(f'n={len(lat)} p50={statistics.median(lat):.1f}ms p95={lat[int(len(lat)*0.95)]:.1f}ms max={lat[-1]:.1f}ms')
"
```

- [ ] **Step 5: Confirm the planner uses the GIN index**

```bash
docker compose exec db psql -U beynunehcheh -c "
EXPLAIN (ANALYZE, BUFFERS)
SELECT id FROM search_searchdocument
WHERE is_active AND vector_en @@ websearch_to_tsquery('english','ministry notice')
LIMIT 500;"
```
Expected: a `Bitmap Index Scan` on `sd_vec_en_gin`. A sequential scan here means the partial-index predicate is not matching the query's `is_active` filter — fix that before recording results.

- [ ] **Step 6: Record the measurements**

Create `docs/superpowers/measurements/2026-08-17-p1-load-test.md` containing: document count, the per-index size table from Step 3, the total relation size, the latency line from Step 4, whether Step 5 showed an index scan, and the Postgres settings in force (`shared_buffers`, `work_mem`). Note the machine it ran on.

Spec 12.6 projects roughly 670 MB total at 51,000 iulaan; this measurement either supports that projection or replaces it. Write down which.

- [ ] **Step 7: Clean up the synthetic rows**

```bash
docker compose exec db psql -U beynunehcheh -c "
DELETE FROM search_searchdocument WHERE source_key LIKE 'synthetic-%';
VACUUM ANALYZE search_searchdocument;"
```

- [ ] **Step 8: Verify the real corpus is intact**

```bash
docker compose exec db psql -U beynunehcheh -c "
SELECT tableoid::regclass AS partition, count(*)
FROM search_searchdocument GROUP BY 1 ORDER BY 1;"
```
Expected: `search_searchdocument_gazette | 306`, `search_searchdocument_ibay | 20445`.

- [ ] **Step 9: Commit**

```bash
jj commit -m "test: 100k document load test and recorded index measurements"
```

---

## Out of scope for this plan

These belong to later phases of the spec and must not be built here:

- Dhivehi normalization, fili handling, transliteration, keyboard-layout decoding (P2, spec 6).
- Attachment fetching and transcription (P3, spec 5.6).
- LLM enrichment, typed attribute schemas, compensation modelling (P4, spec 5).
- The HTTP API, cards for the frontend, query and click logging (P5, spec 9 and 16.3).
- The Next.js frontend (P6, spec 10).
- Dynamic shopping facets, `SpecKey`, `DocumentSpec` (P7, spec 4.4 and 8.3).

One item is deliberately deferred but worth flagging when P2 starts:
`gazette/sync_service.py:20` currently reads `MAX_INDEX_PAGES = 2 if settings.DEBUG else 3500`. The 5,000-listing-page target in spec 5.6.2 needs that to become an explicit setting rather than a `DEBUG` side effect, and the scrape is the long pole for the gazette corpus.
