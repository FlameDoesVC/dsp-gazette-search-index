from django.db import models


class Office(models.Model):
    name = models.CharField(max_length=255, unique=True)
    translated_name = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.translated_name or self.name


class IulaanType(models.Model):
    name = models.CharField(max_length=255, unique=True)
    translated_name = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.translated_name or self.name


class Iulaan(models.Model):
    id = models.CharField(max_length=255, primary_key=True)
    title = models.CharField(max_length=512)
    translated_title = models.CharField(max_length=255, blank=True)
    office = models.ForeignKey(Office, null=True, blank=True, on_delete=models.PROTECT)
    iulaan_type = models.ForeignKey(IulaanType, null=True, blank=True, on_delete=models.PROTECT)
    additional_info = models.JSONField()
    attachments = models.JSONField()
    body = models.TextField()
    translated_body = models.TextField(blank=True)

    def __str__(self):
        return self.title

    @property
    def url(self):
        return f"https://gazette.gov.mv/iulaan/{self.id}"


class Attachment(models.Model):
    """One file attached to an iulaan. Spec 5.6.

    The file itself is never stored: fetch, extract, keep the text and a
    checksum, discard the bytes. Measured, that is 194 MB of text instead of
    ~40 GB of PDFs, and the source URLs are stable and public.
    """

    STATUS = [
        ("pending", "pending"),
        ("ok", "ok"),
        ("ocr_failed", "ocr_failed"),
        ("fetch_failed", "fetch_failed"),
        ("extract_failed", "extract_failed"),
        ("skipped", "skipped"),
    ]
    METHOD = [
        ("docx", "docx"),
        ("pdftotext", "pdftotext"),
        ("transcribed", "transcribed"),
        ("none", "none"),
    ]

    iulaan = models.ForeignKey(
        "gazette.Iulaan", on_delete=models.CASCADE, related_name="attachment_files"
    )
    label_raw = models.CharField(max_length=512, blank=True)
    role = models.CharField(max_length=32, default="unknown")
    url = models.URLField(max_length=1024)
    content_sha = models.CharField(max_length=64, blank=True)
    mime = models.CharField(max_length=128, blank=True)
    size_bytes = models.BigIntegerField(null=True, blank=True)

    text = models.TextField(blank=True)
    page_count = models.IntegerField(null=True, blank=True)
    chars_per_page = models.IntegerField(null=True, blank=True)
    method = models.CharField(max_length=32, choices=METHOD, default="none")
    status = models.CharField(max_length=32, choices=STATUS, default="pending")
    transcribed = models.BooleanField(default=False)
    error = models.TextField(blank=True)
    attempts = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    TEXT_CAP = 20_000

    class Meta:
        unique_together = ("iulaan", "url")
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["iulaan", "role"]),
        ]

    def __str__(self):
        return f"{self.iulaan_id}:{self.role}"
