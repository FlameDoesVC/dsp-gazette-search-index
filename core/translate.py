import asyncio
import hashlib
import logging
import re
import threading

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

OR_URL = "https://openrouter.ai/api/v1/chat/completions"
OR_MODEL = "google/gemma-3-12b-it:free"

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-flash-latest:generateContent"
)

PROMPT_DV_TO_EN = (
    "Translate the following Dhivehi text to English. "
    "Return only the English translation, nothing else."
)

PROMPT_EN_TO_DV = (
    "Translate the following English text to Dhivehi. "
    "Return only the Dhivehi translation, nothing else."
)

CHUNK_SIZE = 4500

BATCH_SIZE = 8
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s*(.*)$")

_BATCH_PROMPT = (
    "Translate each numbered {src} line to {dst}. Output exactly one numbered "
    "line per input, using the same numbering, and nothing else. Do not merge, "
    "split, reorder or omit lines.\n\n"
)


def _parse_numbered(reply: str, expected: int) -> list[str] | None:
    """Return `expected` translations in input order, or None on misalignment.

    Returning None rather than a best guess is deliberate: a batch whose lines
    do not line up would attach each translation to the wrong document, which
    is silent data corruption rather than a visible failure.
    """
    found: dict[int, str] = {}
    for line in (reply or "").splitlines():
        m = _NUMBERED.match(line)
        if m:
            found[int(m.group(1))] = m.group(2).strip()
    if len(found) != expected or set(found) != set(range(1, expected + 1)):
        return None
    return [found[i] for i in range(1, expected + 1)]


def is_dhivehi(text):
    return any("ހ" <= ch <= "޿" for ch in text)


# Basic Latin/Latin-1/Latin Extended (English + accents/digits/punctuation),
# Thaana (Dhivehi), General Punctuation (smart quotes/dashes), plus a few
# Arabic-script punctuation marks borrowed into Dhivehi text (\u060c \u061b \u061f \u06d4).
_CLEAN_TRANSLATION_RE = re.compile(
    "^[\u0000-\u024f\u0780-\u07bf\u2000-\u206f\u060c\u061b\u061f\u06d4\\s]*$"
)


def _is_clean_translation(text):
    return bool(_CLEAN_TRANSLATION_RE.fullmatch(text))


def sentence_boundary(text, max_size=CHUNK_SIZE):
    if len(text) <= max_size:
        return len(text)
    haystack = text[:max_size]
    for sep in ("\n", ".", "،", ",", "۔", " "):
        pos = haystack.rfind(sep)
        if pos > max_size * 0.5:
            return pos + 1
    return max_size

SEMAPHORE = asyncio.Semaphore(2)
CLOUD_SEMAPHORE = asyncio.Semaphore(1)

_client = None
_local_llm = None
_load_lock = threading.Lock()


def _get_client():
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30)
    return _client


def _hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _cached_translation(text):
    from core.models import TranslationCache

    h = _hash(text)
    try:
        return TranslationCache.objects.get(source_hash=h).translated_text
    except TranslationCache.DoesNotExist:
        return None


def _cache_translation(text, translation):
    from core.models import TranslationCache

    TranslationCache.objects.get_or_create(
        source_hash=_hash(text),
        defaults={"translated_text": translation},
    )


async def _cached_translation_async(text):
    from asgiref.sync import sync_to_async

    return await sync_to_async(_cached_translation)(text)


async def _cache_translation_async(text, translation):
    from asgiref.sync import sync_to_async

    await sync_to_async(_cache_translation)(text, translation)


async def _load_local_llm_async():
    return await asyncio.to_thread(_load_local_llm)


def _load_local_llm():
    global _local_llm
    with _load_lock:
        if _local_llm is not None:
            return _local_llm

        from llama_cpp import Llama

        repo_id = "mradermacher/GemmaTranslate-v3-12B-GGUF"
        filename = "GemmaTranslate-v3-12B.IQ4_XS.gguf"

        logger.info("Downloading GemmaTranslate model (~7GB, one-time)...")
        print("\n--- Downloading translation model (~7GB, one-time) ---")

        try:
            from huggingface_hub import hf_hub_download
            path = hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                resume_download=True,
            )
            print(f"Model downloaded to: {path}")
            _local_llm = Llama(
                model_path=path,
                n_ctx=4096,
                verbose=False,
            )
        except ImportError:
            _local_llm = Llama.from_pretrained(
                repo_id=repo_id,
                filename=filename,
                n_ctx=4096,
                verbose=True,
            )

        logger.info("Local model ready.")
        print("--- Model ready, translations starting ---\n")
        return _local_llm


def _get_local_llm():
    if _local_llm is not None:
        return _local_llm
    return _load_local_llm()


async def _translate_local(text, prompt):
    llm = await _load_local_llm_async()
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        lambda: llm.create_chat_completion(
            messages=[{"role": "user", "content": f"{prompt}\n\n{text}"}],
            temperature=0,
            max_tokens=1024,
        ),
    )
    return result["choices"][0]["message"]["content"].strip()


async def _translate_ollama(text, prompt):
    try:
        response = await _get_client().post(
            f"{settings.OLLAMA_URL}/api/chat",
            json={
                "model": settings.OLLAMA_MODEL,
                "messages": [{"role": "user", "content": f"{prompt}\n\n{text}"}],
                "stream": False,
                "options": {"temperature": 0, "seed": 42},
            },
            timeout=120,
        )
        if response.status_code != 200:
            logger.warning("Ollama %d: %s", response.status_code, response.text[:200])
            return None
        data = response.json()
        return data["message"]["content"].strip()
    except Exception:
        logger.warning("Ollama failed: %s...", text[:80], exc_info=True)
        return None


async def _translate_openrouter(text, prompt):
    for attempt in range(5):
        try:
            response = await _get_client().post(
                OR_URL,
                headers={
                    "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OR_MODEL,
                    "messages": [{"role": "user", "content": f"{prompt}\n\n{text}"}],
                    "temperature": 0,
                },
            )
            if response.status_code == 429:
                delay = 2 ** attempt * 4
                logger.warning("OpenRouter 429, waiting %ds...", delay)
                await asyncio.sleep(delay)
                continue
            if response.status_code != 200:
                logger.warning("OpenRouter %d: %s", response.status_code, response.text[:200])
                return None
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
        except httpx.TimeoutException:
            logger.warning("OpenRouter timed out, retrying...")
            continue
        except Exception:
            logger.warning("OpenRouter failed: %s...", text[:80], exc_info=True)
            return None
    return None


async def _translate_gemini(text, prompt):
    for attempt in range(5):
        try:
            response = await _get_client().post(
                GEMINI_URL,
                headers={
                    "X-goog-api-key": settings.GEMINI_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "contents": [{"parts": [{"text": f"{prompt}\n\n{text}"}]}],
                    "generationConfig": {"temperature": 0},
                },
            )
            if response.status_code == 429:
                delay = 2 ** attempt * 4
                logger.warning("Gemini 429, waiting %ds...", delay)
                await asyncio.sleep(delay)
                continue
            if response.status_code != 200:
                logger.warning("Gemini %d: %s", response.status_code, response.text[:200])
                return None
            data = response.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except httpx.TimeoutException:
            logger.warning("Gemini timed out, retrying...")
            continue
        except Exception:
            logger.warning("Gemini failed: %s...", text[:80], exc_info=True)
            return None
    return None


_inflight = {}


async def _translate(text, prompt):
    if not text or not text.strip():
        return ""

    text = text.strip()[:3500]

    cached = await _cached_translation_async(text)
    if cached:
        return cached

    key = (prompt, _hash(text))
    if key in _inflight:
        return await _inflight[key]

    task = asyncio.ensure_future(_translate_uncached(text, prompt))
    _inflight[key] = task
    try:
        return await task
    finally:
        _inflight.pop(key, None)


async def _translate_uncached(text, prompt):
    result = None
    async with SEMAPHORE:
        if settings.OLLAMA_URL:
            result = await _translate_ollama(text, prompt)
        else:
            try:
                result = await _translate_local(text, prompt)
            except Exception:
                logger.debug("Local model unavailable, trying remote")
                result = None

    if result is not None and not _is_clean_translation(result):
        logger.warning("Local translation had unexpected script, discarding: %s", result[:80])
        result = None

    if result is None:
        async with CLOUD_SEMAPHORE:
            try:
                if settings.OPENROUTER_API_KEY:
                    result = await _translate_openrouter(text, prompt)
                if result is None and settings.GEMINI_API_KEY:
                    result = await _translate_gemini(text, prompt)
            except Exception:
                result = None

    if result and result != text.strip():
        await _cache_translation_async(text, result)
        return result
    return ""


async def translate_dv_to_en(text):
    return await _translate(text, PROMPT_DV_TO_EN)


async def translate_en_to_dv(text):
    return await _translate(text, PROMPT_EN_TO_DV)


async def translate_auto(text):
    if not text or not text.strip():
        return ""
    if is_dhivehi(text):
        return await translate_dv_to_en(text)
    return await translate_en_to_dv(text)


def _translate_sync(text, prompt):
    if not text or not text.strip():
        return None

    text = text.strip()[:3500]

    cached = _cached_translation(text)
    if cached:
        return cached

    result = _chat(f"{prompt}\n\n{text}")

    if result is not None and not _is_clean_translation(result):
        logger.warning("Local translation had unexpected script, discarding: %s", result[:80])
        result = None

    if result and result != text.strip():
        _cache_translation(text, result)
        return result
    return None


def translate_dv_to_en_sync(text):
    return _translate_sync(text, PROMPT_DV_TO_EN)


def translate_en_to_dv_sync(text):
    return _translate_sync(text, PROMPT_EN_TO_DV)


def translate_auto_sync(text):
    if not text or not text.strip():
        return None
    return translate_dv_to_en_sync(text) if is_dhivehi(text) else translate_en_to_dv_sync(text)


def _translate_batch_chunk(chunk: list[str], prompt: str, src: str, dst: str,
                           batch_size: int) -> list[str]:
    """Translate one chunk (<= batch_size) in a single numbered call.

    Cache per item, never per batch -- batches never repeat, individual
    strings do (40% of iBay titles are duplicates). On a misaligned reply,
    fall back to one call per uncached item rather than trusting position.
    """
    results: list[str] = [_cached_translation(t) or "" for t in chunk]
    missing_idx = [i for i, r in enumerate(results) if not r]
    if not missing_idx:
        return results

    numbered = "\n".join(f"{i + 1}. {chunk[i]}" for i in missing_idx)
    reply = _chat(_BATCH_PROMPT.format(src=src, dst=dst) + numbered)
    if reply:
        parsed = _parse_numbered(reply, len(missing_idx))
        if parsed is not None:
            for k, idx in enumerate(missing_idx):
                results[idx] = parsed[k]
                _cache_translation(chunk[idx], parsed[k])
            return results

    # Misaligned or empty reply: pay for accuracy, one call per miss.
    for idx in missing_idx:
        results[idx] = _translate_sync(chunk[idx], prompt) or chunk[idx].strip()
    return results


def translate_batch_sync(texts: list[str], *, target: str,
                         batch_size: int = BATCH_SIZE) -> list[str]:
    """Translate a list of short strings, one numbered call per batch.

    Measured 7.7x faster than one call per title, and more accurate: numbered
    context disambiguates ("ނީލަން ކިޔުން" -> "Public auction" in a batch, but
    "Niland Reading" alone). Never fewer results than inputs.
    """
    if not texts:
        return []
    prompt = PROMPT_DV_TO_EN if target == "en" else PROMPT_EN_TO_DV
    src = "Dhivehi" if target == "en" else "English"
    dst = "English" if target == "en" else "Dhivehi"

    out: list[str] = []
    for start in range(0, len(texts), batch_size):
        out.extend(_translate_batch_chunk(
            texts[start:start + batch_size], prompt, src, dst, batch_size))
    return out


async def translate_batch(texts: list[str], *, target: str,
                          batch_size: int = BATCH_SIZE) -> list[str]:
    """Async wrapper over translate_batch_sync, matching translate_auto."""
    from asgiref.sync import sync_to_async
    return await sync_to_async(translate_batch_sync)(texts, target=target,
                                                     batch_size=batch_size)


def _translate_ollama_sync(content):
    try:
        response = httpx.post(
            f"{settings.OLLAMA_URL}/api/chat",
            json={
                "model": settings.OLLAMA_MODEL,
                "messages": [{"role": "user", "content": content}],
                "stream": False,
                "options": {"temperature": 0, "seed": 42},
            },
            timeout=120,
        )
        if response.status_code != 200:
            return None
        data = response.json()
        result = data["message"]["content"].strip()
        return result if result and result != content.strip() else None
    except Exception:
        return None


def _translate_openrouter_sync(content):
    for attempt in range(5):
        try:
            response = httpx.post(
                OR_URL,
                headers={
                    "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OR_MODEL,
                    "messages": [{"role": "user", "content": content}],
                    "temperature": 0,
                },
                timeout=30,
            )
            if response.status_code == 429:
                import time
                time.sleep(2 ** attempt * 4)
                continue
            if response.status_code != 200:
                return None
            data = response.json()
            result = data["choices"][0]["message"]["content"].strip()
            return result if result and result != content.strip() else None
        except Exception:
            return None
    return None


def _translate_gemini_sync(content):
    for attempt in range(5):
        try:
            response = httpx.post(
                GEMINI_URL,
                headers={
                    "X-goog-api-key": settings.GEMINI_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "contents": [{"parts": [{"text": content}]}],
                    "generationConfig": {"temperature": 0},
                },
                timeout=30,
            )
            if response.status_code == 429:
                import time
                time.sleep(2 ** attempt * 4)
                continue
            if response.status_code != 200:
                return None
            data = response.json()
            result = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            return result if result and result != content.strip() else None
        except Exception:
            return None
    return None


def _local_chat(content):
    try:
        llm = _get_local_llm()
        result = llm.create_chat_completion(
            messages=[{"role": "user", "content": content}],
            temperature=0,
            max_tokens=1024,
        )
        return result["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def _chat(full_prompt: str, **kw) -> str | None:
    """One raw provider round trip for a complete prompt, via the escalation
    ladder: ollama (or local llm) -> openrouter -> gemini.

    No clean-check and no cache here -- callers decide those. Used by both the
    per-item `_translate_sync` and the numbered `translate_batch_sync`, so the
    ladder exists once.
    """
    result = _translate_ollama_sync(full_prompt) if settings.OLLAMA_URL \
        else _local_chat(full_prompt)
    if result is None and settings.OPENROUTER_API_KEY:
        result = _translate_openrouter_sync(full_prompt)
    if result is None and settings.GEMINI_API_KEY:
        result = _translate_gemini_sync(full_prompt)
    return result
