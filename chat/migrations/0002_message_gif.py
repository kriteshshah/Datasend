# Generated manually for GIF picker (external CDN URLs)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="gif_url",
            field=models.URLField(
                blank=True,
                help_text="External GIF (Tenor/Giphy CDN) when message_type is gif",
                max_length=2048,
            ),
        ),
        migrations.AlterField(
            model_name="message",
            name="message_type",
            field=models.CharField(
                choices=[
                    ("text", "Text"),
                    ("image", "Image"),
                    ("video", "Video"),
                    ("doc", "Document"),
                    ("emoji", "Emoji Only"),
                    ("gif", "GIF"),
                    ("system", "System"),
                ],
                default="text",
                max_length=10,
            ),
        ),
    ]
