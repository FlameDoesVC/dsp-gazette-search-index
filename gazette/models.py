from django.db import models

# Create your models here.
class Iulaan(models.Model):
    id = models.CharField(max_length=255, primary_key=True)
    title = models.CharField(max_length=255)
    translated_title = models.CharField(max_length=255, blank=True)
    office_name = models.CharField(max_length=255)
    translated_office_name = models.CharField(max_length=255, blank=True)
    iulaan_type = models.CharField(max_length=255)
    additional_info = models.JSONField()
    attachments = models.JSONField()
    body = models.TextField()
    translated_body = models.TextField(blank=True)

    def __str__(self):
        return self.title
    
    @property
    def url(self):
        return f"https://gazette.gov.mv/iulaan/{self.id}"