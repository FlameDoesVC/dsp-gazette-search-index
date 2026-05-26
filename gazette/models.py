from django.db import models

# Create your models here.
class Iulaan(models.Model):
    id = models.CharField(max_length=255, primary_key=True)
    title = models.CharField(max_length=255)
    office_name = models.CharField(max_length=255)
    iulaan_type = models.CharField(max_length=255)
    additional_info = models.JSONField()
    attachments = models.JSONField()
    body = models.TextField()

    def __str__(self):
        return self.title
    
    @property
    def url(self):
        return f"https://gazette.gov.mv/iulaan/{self.id}"