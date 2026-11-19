#!/usr/bin/env python3
"""
RedFoxRHH - RedheadHeaven New Post

Google Drive:
Bleusky Photos / Redheadheaven / Redfox / Ready
Bleusky Photos / Redheadheaven / Redfox / Used
"""

import io
import json
import os
import random
import subprocess
import tempfile
from pathlib import Path
from shutil import which

from atproto import Client, models
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from PIL import Image, ImageOps

# --------------------------------------------------
# SETTINGS
# --------------------------------------------------

BLUESKY_HANDLE = "redheadheaven.bsky.social"
BLUESKY_PASSWORD = os.getenv("RHH_PASSWORD", "").strip()

# Google Drive folder ID
REDHEADHEAVEN_FOLDER_ID = "1io9JflAesjxaTXtCSZbQJeY-UUfmF-yb"

CAPTION = (
    "♥️ @redfoxofficial.bsky.social ♥️\n"
    "🦊 https://onlyfans.com/redfoxofficial 🦊"
)

MAX_IMAGE_BYTES = 1_900_000
MAX_IMAGE_DIMENSION = 2400

MAX_VIDEO_SECONDS = 180
MAX_VIDEO_BYTES = 95 * 1024 * 1024

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

# --------------------------------------------------
# GOOGLE DRIVE
# --------------------------------------------------

def drive_client():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

    if not raw:
        raise RuntimeError(
            "GitHub Secret GOOGLE_SERVICE_ACCOUNT_JSON ontbreekt."
        )

    info = json.loads(raw)

    credentials = service_account.Credentials.from_service_account_info(
        info,
        scopes=DRIVE_SCOPES
    )

    return build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False
    )


def get_folder_metadata(drive, folder_id):
    return drive.files().get(
        fileId=folder_id,
        fields="id,name,mimeType",
        supportsAllDrives=True
    ).execute()


def find_child_folder(drive, parent_id, folder_name):
    query = (
        f"'{parent_id}' in parents and "
        f"name = '{folder_name}' and "
        "mimeType = 'application/vnd.google-apps.folder' "
        "and trashed = false"
    )

    result = drive.files().list(
        q=query,
        fields="files(id,name)",
        pageSize=100,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()

    folders = result.get("files", [])

    if not folders:
        parent = get_folder_metadata(drive, parent_id)

        raise RuntimeError(
            f"Map '{folder_name}' niet gevonden in "
            f"'{parent.get('name')}'."
        )

    return folders[0]["id"]


def resolve_redfox_folder(drive):
    configured = get_folder_metadata(
        drive,
        REDHEADHEAVEN_FOLDER_ID
    )

    folder_name = configured.get("name", "")

    if folder_name.lower() == "redfox":
        print("Redfox-map rechtstreeks gevonden.")
        return REDHEADHEAVEN_FOLDER_ID

    if folder_name.lower() == "redheadheaven":
        print("Redheadheaven gevonden.")

        return find_child_folder(
            drive,
            REDHEADHEAVEN_FOLDER_ID,
            "Redfox"
        )

    if folder_name.lower() == "bleusky photos":
        print("Bleusky Photos gevonden.")

        redheadheaven_id = find_child_folder(
            drive,
            REDHEADHEAVEN_FOLDER_ID,
            "Redheadheaven"
        )

        return find_child_folder(
            drive,
            redheadheaven_id,
            "Redfox"
        )

    return find_child_folder(
        drive,
        REDHEADHEAVEN_FOLDER_ID,
        "Redfox"
    )


def list_ready_files(drive, ready_id):
    query = (
        f"'{ready_id}' in parents "
        "and trashed = false"
    )

    result = drive.files().list(
        q=query,
        fields="files(id,name,mimeType,size)",
        pageSize=1000,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()

    allowed = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

    return [
        item for item in result.get("files", [])
        if Path(item["name"]).suffix.lower() in allowed
    ]


def download_drive_file(drive, file_id, destination):
    request = drive.files().get_media(
        fileId=file_id,
        supportsAllDrives=True
    )

    with open(destination, "wb") as handle:
        downloader = MediaIoBaseDownload(
            handle,
            request
        )

        done = False

        while not done:
            _, done = downloader.next_chunk()


def move_to_used(drive, file_id, ready_id, used_id):
    drive.files().update(
        fileId=file_id,
        addParents=used_id,
        removeParents=ready_id,
        supportsAllDrives=True,
        fields="id,parents"
    ).execute()


# --------------------------------------------------
# IMAGE PROCESSING - PILLOW
# --------------------------------------------------

def prepare_image(source_path):
    image = Image.open(source_path)

    # Correct camera orientation
    image = ImageOps.exif_transpose(image)

    # Convert to RGB for JPEG upload
    if image.mode != "RGB":
        image = image.convert("RGB")

    # Keep original aspect ratio
    image.thumbnail(
        (MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION),
        Image.Resampling.LANCZOS
    )

    quality = 90

    while True:
        output = io.BytesIO()

        image.save(
            output,
            format="JPEG",
            quality=quality,
            optimize=True
        )

        data = output.getvalue()

        if len(data) <= MAX_IMAGE_BYTES:
            return data, image.width, image.height

        if quality > 55:
            quality -= 7

        else:
            new_width = max(500, int(image.width * 0.85))
            new_height = max(500, int(image.height * 0.85))

            image.thumbnail(
                (new_width, new_height),
                Image.Resampling.LANCZOS
            )

            quality = 82

        if (
            image.width <= 500
            and image.height <= 500
            and len(data) > MAX_IMAGE_BYTES
        ):
            raise RuntimeError(
                "Afbeelding kan niet onder de uploadlimiet worden gebracht."
            )


# --------------------------------------------------
# VIDEO PROCESSING - FFMPEG
# --------------------------------------------------

def prepare_video(source_path):
    if not which("ffmpeg") or not which("ffprobe"):
        raise RuntimeError("FFmpeg/FFprobe ontbreekt.")

    with tempfile.TemporaryDirectory() as temp_dir:

        output_path = str(
            Path(temp_dir) / "ready.mp4"
        )

        probe = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                source_path
            ],
            capture_output=True,
            text=True,
            check=True
        )

        duration = float(probe.stdout.strip() or "0")

        if duration <= 0 or duration > MAX_VIDEO_SECONDS:
            raise RuntimeError(
                f"Video duurt {duration:.1f}s. "
                f"Maximum is {MAX_VIDEO_SECONDS}s."
            )

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i", source_path,
                "-vf",
                "scale='min(1920,iw)':'min(1920,ih)':"
                "force_original_aspect_ratio=decrease",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "27",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                output_path
            ],
            capture_output=True,
            text=True,
            check=True
        )

        if os.path.getsize(output_path) > MAX_VIDEO_BYTES:
            raise RuntimeError(
                "Video is groter dan de uploadlimiet."
            )

        return Path(output_path).read_bytes()


# --------------------------------------------------
# BLUESKY FACETS
# --------------------------------------------------

def build_facets(text, client):
    facets = []

    mention = "@redfoxofficial.bsky.social"
    mention_start = text.index(mention)

    did = client.com.atproto.identity.resolve_handle(
        {"handle": "redfoxofficial.bsky.social"}
    ).did

    facets.append(
        models.AppBskyRichtextFacet.Main(
            index=models.AppBskyRichtextFacet.ByteSlice(
                byte_start=len(
                    text[:mention_start].encode("utf-8")
                ),
                byte_end=len(
                    text[:mention_start + len(mention)].encode("utf-8")
                )
            ),
            features=[
                models.AppBskyRichtextFacet.Mention(
                    did=did
                )
            ]
        )
    )

    link = "https://onlyfans.com/redfoxofficial"
    link_start = text.index(link)

    facets.append(
        models.AppBskyRichtextFacet.Main(
            index=models.AppBskyRichtextFacet.ByteSlice(
                byte_start=len(
                    text[:link_start].encode("utf-8")
                ),
                byte_end=len(
                    text[:link_start + len(link)].encode("utf-8")
                )
            ),
            features=[
                models.AppBskyRichtextFacet.Link(
                    uri=link
                )
            ]
        )
    )

    return facets


# --------------------------------------------------
# BLUESKY PUBLISHING
# --------------------------------------------------

def publish_image(
    client,
    image_bytes,
    facets,
    width,
    height
):
    blob = client.upload_blob(image_bytes).blob

    image = models.AppBskyEmbedImages.Image(
        alt="RedFox",
        image=blob,
        aspect_ratio=models.AppBskyEmbedDefs.AspectRatio(
            width=width,
            height=height
        )
    )

    embed = models.AppBskyEmbedImages.Main(
        images=[image]
    )

    return client.send_post(
        text=CAPTION,
        facets=facets,
        embed=embed
    )


def publish_video(client, video_bytes, filename, facets):
    upload_result = client.upload_video(
        video=video_bytes,
        video_name=filename,
        video_alt="RedFox"
    )

    video_blob = getattr(
        upload_result,
        "blob",
        upload_result
    )

    embed = models.AppBskyEmbedVideo.Main(
        video=video_blob,
        alt="RedFox"
    )

    return client.send_post(
        text=CAPTION,
        facets=facets,
        embed=embed
    )


# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main():

    if not BLUESKY_PASSWORD:
        raise RuntimeError(
            "GitHub Secret RHH_PASSWORD ontbreekt."
        )

    drive = drive_client()

    redfox_id = resolve_redfox_folder(drive)

    ready_id = find_child_folder(
        drive,
        redfox_id,
        "Ready"
    )

    used_id = find_child_folder(
        drive,
        redfox_id,
        "Used"
    )

    candidates = list_ready_files(
        drive,
        ready_id
    )

    if not candidates:
        print("Geen geschikte foto of video in Ready.")
        return

    selected = random.choice(candidates)

    filename = selected["name"]
    extension = Path(filename).suffix.lower()

    print(f"Geselecteerd bestand: {filename}")

    client = Client()

    client.login(
        BLUESKY_HANDLE,
        BLUESKY_PASSWORD
    )

    facets = build_facets(
        CAPTION,
        client
    )

    with tempfile.TemporaryDirectory() as temp_dir:

        local_path = str(
            Path(temp_dir) / filename
        )

        download_drive_file(
            drive,
            selected["id"],
            local_path
        )

        if extension in IMAGE_EXTENSIONS:

            media, width, height = prepare_image(
                local_path
            )

            print(
                f"Foto aangepast: {width}x{height} pixels"
            )

            publish_image(
                client,
                media,
                facets,
                width,
                height
            )

        elif extension in VIDEO_EXTENSIONS:

            media = prepare_video(
                local_path
            )

            publish_video(
                client,
                media,
                filename,
                facets
            )

        else:
            raise RuntimeError(
                f"Niet-ondersteund bestandstype: {extension}"
            )

    # Move only after successful Bluesky publication
    move_to_used(
        drive,
        selected["id"],
        ready_id,
        used_id
    )

    print(
        f"Succesvol gepubliceerd en verplaatst naar Used: {filename}"
    )


if __name__ == "__main__":
    main()