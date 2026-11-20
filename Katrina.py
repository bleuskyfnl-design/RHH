
import os
import io
import json
import random
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from atproto import Client, client_utils, models
from PIL import Image, ImageOps

# ============================================================
# KATRINA - REDHEADHEAVEN
# Gebaseerd op de RedFox RHH technische werkwijze
# ============================================================

DRIVE_ROOT_ID = "1A13epzF0oc2Ol3Wg4b_MdRybbmoMKQ3E"
READY_FOLDER_ID = "1Rh6ZsbOxdyMJ6jXtNw_dq23fGv_mR2dl"

TARGET_ACCOUNT = "redheadheaven.bsky.social"
PASSWORD_SECRET = "RHH_PASSWORD"

STATE_FILE = "katrina_redhead_state.json"

WEBSITE_URL = "https://Yourgirlkatrina.com"
MENTION_HANDLE = "hotgirlkatrina.bsky.social"

CAPTION_TITLE = "🌸 𝐊𝐀𝐓𝐑𝐈𝐍𝐀 🌸"

ALT_TEXT = (
    "🧡🖤 Photo from Katrina's #redhead collection. "
    "Exclusive content on #OnlyFans #OF"
)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}

MAX_IMAGE_DIMENSION = 2400
MAX_IMAGE_SIZE = 1_900_000

MAX_VIDEO_SIZE = 95_000_000
MAX_VIDEO_SECONDS = 180
MAX_VIDEO_DIMENSION = 1920

SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_MIME = "application/vnd.google-apps.folder"


# ============================================================
# GOOGLE DRIVE
# ============================================================

def connect_drive():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

    if not raw:
        raise RuntimeError(
            "Missing GitHub Secret: GOOGLE_SERVICE_ACCOUNT_JSON"
        )

    credentials = service_account.Credentials.from_service_account_info(
        json.loads(raw),
        scopes=SCOPES
    )

    return build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False
    )


def get_folder(service, folder_id):
    return service.files().get(
        fileId=folder_id,
        fields="id,name,mimeType,parents",
        supportsAllDrives=True
    ).execute()


def list_children(service, folder_id):
    results = []
    page_token = None

    while True:
        response = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            pageSize=1000,
            pageToken=page_token,
            fields=(
                "nextPageToken,"
                "files(id,name,mimeType,size,parents,appProperties)"
            ),
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()

        results.extend(response.get("files", []))
        page_token = response.get("nextPageToken")

        if not page_token:
            break

    return results


def find_or_create_folder(service, name, parent_id):
    for item in list_children(service, parent_id):
        if (
            item["mimeType"] == FOLDER_MIME
            and item["name"] == name
        ):
            return item["id"]

    folder = service.files().create(
        body={
            "name": name,
            "mimeType": FOLDER_MIME,
            "parents": [parent_id]
        },
        fields="id",
        supportsAllDrives=True
    ).execute()

    print(f"📁 Created folder: {name}")
    return folder["id"]


def ensure_hotties_structure(service, categories):
    hotties_id = find_or_create_folder(
        service, "Hotties", READY_FOLDER_ID
    )

    hotties_ready_id = find_or_create_folder(
        service, "Ready", hotties_id
    )

    hotties_used_id = find_or_create_folder(
        service, "Used", hotties_id
    )

    category_destinations = {}

    for category in categories:
        category_name = category["name"]

        category_destinations[category_name] = {
            "ready": find_or_create_folder(
                service, category_name, hotties_ready_id
            ),
            "used": find_or_create_folder(
                service, category_name, hotties_used_id
            )
        }

    return category_destinations


def download_file(service, file_id):
    request = service.files().get_media(
        fileId=file_id,
        supportsAllDrives=True
    )

    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)

    done = False

    while not done:
        _, done = downloader.next_chunk()

    return buffer.getvalue()


def move_file(service, file_id, destination_id):
    metadata = service.files().get(
        fileId=file_id,
        fields="parents",
        supportsAllDrives=True
    ).execute()

    parents = metadata.get("parents", [])
    remove_parents = ",".join(parents)

    service.files().update(
        fileId=file_id,
        addParents=destination_id,
        removeParents=remove_parents,
        supportsAllDrives=True
    ).execute()


def save_drive_properties(service, file_id, properties):
    service.files().update(
        fileId=file_id,
        body={"appProperties": properties},
        supportsAllDrives=True
    ).execute()


# ============================================================
# BLUESKY LOGIN
# ============================================================


def connect_bluesky():
    password = os.environ.get(PASSWORD_SECRET, "").strip()

    if not password:
        raise RuntimeError(
            f"Missing GitHub Secret: {PASSWORD_SECRET}"
        )

    client = Client()

    import atproto
    print("ATPROTO VERSION:", atproto.__version__)
    print("CLIENT TYPE:", type(client))
    print(
        "UPLOAD VIDEO AVAILABLE:",
        hasattr(client, "upload_video")
    )

    client.login(TARGET_ACCOUNT, password)

    print(f"✅ Logged in as {TARGET_ACCOUNT}")
    return client
 ============================================================
# CAPTION AND CLICKABLE LINKS
# ============================================================

def build_caption(client):
    builder = client_utils.TextBuilder()

    builder.text(CAPTION_TITLE + "\n\n")

    mention_did = client.resolve_handle(MENTION_HANDLE).did

    builder.mention(
        f"🩷 @{MENTION_HANDLE} 🩷",
        mention_did
    )

    builder.text("\n\n")

    builder.link(
        "❤️ Yourgirlkatrina.com ❤️",
        WEBSITE_URL
    )

    return builder


# ============================================================
# MEDIA SELECTION
# ============================================================

def get_categories(service):
    children = list_children(service, READY_FOLDER_ID)
    categories = []

    for item in children:
        if item["mimeType"] != FOLDER_MIME:
            continue

        if item["name"].lower() == "hotties":
            continue

        categories.append(item)

    return categories


def get_media_candidates(service, categories):
    candidates = []

    for category in categories:
        files = list_children(service, category["id"])

        for file_info in files:
            if file_info["mimeType"] == FOLDER_MIME:
                continue

            extension = Path(file_info["name"]).suffix.lower()

            if extension not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
                continue

            properties = file_info.get("appProperties") or {}

            # Voorkom dubbele publicaties.
            if properties.get("redhead_posted_at"):
                continue

            candidates.append({
                "file": file_info,
                "category": category["name"],
                "category_id": category["id"]
            })

    return candidates


# ============================================================
# IMAGE PREPARATION
# Geen balken en geen onnodige uitsnede.
# Originele beeldverhouding blijft behouden.
# ============================================================

def prepare_image(data):
    with Image.open(io.BytesIO(data)) as opened_image:
        image = ImageOps.exif_transpose(opened_image)
        image.load()

        if image.mode in ("RGBA", "LA") or "transparency" in image.info:
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, "white")
            background.paste(
                rgba,
                mask=rgba.getchannel("A")
            )
            image = background
        else:
            image = image.convert("RGB")

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

        result = output.getvalue()

        if len(result) <= MAX_IMAGE_SIZE:
            print(
                f"✅ Image prepared: {image.width}x{image.height}, "
                f"{len(result)} bytes"
            )
            return result, image.width, image.height

        if quality > 50:
            quality -= 5
            continue

        width, height = image.size

        if max(width, height) <= 600:
            raise RuntimeError(
                "Image could not be compressed below 1.9 MB."
            )

        image.thumbnail(
            (
                max(1, int(width * 0.85)),
                max(1, int(height * 0.85))
            ),
            Image.Resampling.LANCZOS
        )

        quality = 82


# ============================================================
# VIDEO PREPARATION
# H.264 / AAC MP4 voor Bluesky.
# Behoudt beeldverhouding en snijdt niets uit.
#
# FIX:
# - Alleen eerste videostream gebruiken.
# - Alleen eerste audiostream gebruiken.
# - Apple APAC en overige extra streams overslaan.
# - Geen onnodige data-, subtitle- of metadata-streams.
# ============================================================

def ensure_ffmpeg():
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return

    print("📦 Installing FFmpeg...")

    subprocess.run(
        ["sudo", "apt-get", "update"],
        check=True
    )

    subprocess.run(
        ["sudo", "apt-get", "install", "-y", "ffmpeg"],
        check=True
    )

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError(
            "FFmpeg or FFprobe installation failed."
        )


def get_video_duration(input_path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            input_path
        ],
        check=True,
        capture_output=True,
        text=True
    )

    try:
        return float(result.stdout.strip())
    except (ValueError, TypeError):
        raise RuntimeError(
            "Could not determine video duration."
        )


def prepare_video(data, filename):
    ensure_ffmpeg()

    with tempfile.TemporaryDirectory() as temp_dir:
        safe_name = Path(filename).name
        extension = Path(safe_name).suffix.lower() or ".mov"

        input_path = os.path.join(
            temp_dir,
            "katrina_original" + extension
        )

        output_path = os.path.join(
            temp_dir,
            "katrina_ready.mp4"
        )

        with open(input_path, "wb") as file:
            file.write(data)

        duration = get_video_duration(input_path)

        print(f"⏱️ Original video duration: {duration:.1f} seconds")

        if duration <= 0:
            raise RuntimeError("Invalid video duration.")

        if duration > MAX_VIDEO_SECONDS:
            raise RuntimeError(
                f"Video is {duration:.1f} seconds. "
                f"Maximum allowed is {MAX_VIDEO_SECONDS} seconds. "
                "Original Drive file has not been moved."
            )

        # Behoud volledige beeldverhouding.
        # Geen crop, geen zwarte balken.
        scale_filter = (
            "scale="
            "w='min(1920,iw)':"
            "h='min(1920,ih)':"
            "force_original_aspect_ratio=decrease:"
            "force_divisible_by=2"
        )

        command = [
            "ffmpeg",
            "-y",

            # Input
            "-analyzeduration", "100M",
            "-probesize", "100M",
            "-i", input_path,

            # Alleen de eerste videostream.
            "-map", "0:v:0",

            # Alleen de eerste audiostream (optioneel).
            # Hierdoor wordt Apple APAC niet geselecteerd.
            "-map", "0:a:0?",

            # Onbekende streams negeren.
            "-ignore_unknown",

            # Video-instellingen
            "-t", str(MAX_VIDEO_SECONDS),
            "-vf", scale_filter,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",

            # Audio-instellingen
            "-c:a", "aac",
            "-b:a", "128k",
            "-ac", "2",

            # MP4-optimalisatie
            "-movflags", "+faststart",

            # Geen ondertiteling of data.
            "-sn",
            "-dn",

            output_path
        ]

        print("🎬 Converting video for Bluesky...")
        print("📐 Preserving original aspect ratio...")
        print("🔊 Selecting first audio stream only...")
        print("🛡️ Ignoring unsupported Apple audio/data streams...")

        result = subprocess.run(
            command,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            print("❌ FFmpeg conversion failed.")
            print("----- FFmpeg error output -----")
            print(result.stderr[-12000:])
            print("----- End FFmpeg error output -----")

            raise RuntimeError(
                f"FFmpeg conversion failed with exit code "
                f"{result.returncode}."
            )

        if not os.path.exists(output_path):
            raise RuntimeError(
                "FFmpeg did not create the output MP4."
            )

        if os.path.getsize(output_path) <= 0:
            raise RuntimeError(
                "FFmpeg created an empty output MP4."
            )

        with open(output_path, "rb") as file:
            video_data = file.read()

    if not video_data:
        raise RuntimeError("Converted video is empty.")

    if len(video_data) > MAX_VIDEO_SIZE:
        raise RuntimeError(
            f"Converted video is {len(video_data)} bytes, "
            "larger than the 95 MB limit. "
            "Original Drive file has not been moved."
        )

    print(
        f"✅ Video prepared: {len(video_data)} bytes "
        f"({len(video_data) / 1024 / 1024:.2f} MB)"
    )

    return video_data


# ============================================================
# PUBLISHING
# ============================================================

def publish_image(client, media_data, width, height):
    caption = build_caption(client)

    uploaded = client.upload_blob(media_data).blob

    image = models.AppBskyEmbedImages.Image(
        alt=ALT_TEXT,
        image=uploaded,
        aspect_ratio=models.AppBskyEmbedDefs.AspectRatio(
            width=width,
            height=height
        )
    )

    embed = models.AppBskyEmbedImages.Main(
        images=[image]
    )

    return client.send_post(
        text=caption,
        embed=embed
    )


def publish_video(client, video_data, filename):
    caption = build_caption(client)

    upload_response = client.upload_video(
        video=video_data,
        video_name=Path(filename).stem + ".mp4",
        video_alt=ALT_TEXT
    )

    video_blob = getattr(
        upload_response,
        "blob",
        upload_response
    )

    embed = models.AppBskyEmbedVideo.Main(
        video=video_blob,
        alt=ALT_TEXT
    )

    return client.send_post(
        text=caption,
        embed=embed
    )


def publish_media(client, media_data, filename):
    extension = Path(filename).suffix.lower()

    if extension in IMAGE_EXTENSIONS:
        print("🖼️ Preparing photo...")

        image_data, width, height = prepare_image(media_data)

        return publish_image(
            client,
            image_data,
            width,
            height
        )

    if extension in VIDEO_EXTENSIONS:
        print("🎬 Preparing video...")

        video_data = prepare_video(
            media_data,
            filename
        )

        return publish_video(
            client,
            video_data,
            filename
        )

    raise ValueError(
        f"Unsupported media: {filename}"
    )


# ============================================================
# STATE
# ============================================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}

    with open(STATE_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(
            state,
            file,
            indent=2,
            ensure_ascii=False
        )


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 60)
    print("          KATRINA - REDHEADHEAVEN")
    print("=" * 60)

    print("🔐 Connecting Google Drive...")
    drive = connect_drive()

    root = get_folder(drive, DRIVE_ROOT_ID)
    ready = get_folder(drive, READY_FOLDER_ID)

    if root["name"].lower() != "katrina":
        raise RuntimeError(
            f"Wrong root folder: expected Katrina, got {root['name']}"
        )

    if ready["name"].lower() != "ready":
        raise RuntimeError(
            f"Wrong ready folder: expected ready, got {ready['name']}"
        )

    if DRIVE_ROOT_ID not in ready.get("parents", []):
        raise RuntimeError(
            "The ready folder is not directly inside Katrina."
        )

    print("✅ Katrina and Ready folders confirmed.")

    categories = get_categories(drive)

    if not categories:
        raise RuntimeError(
            "No category folders found in Katrina/Ready."
        )

    print(f"📂 Found {len(categories)} categories.")

    destinations = ensure_hotties_structure(
        drive,
        categories
    )

    candidates = get_media_candidates(
        drive,
        categories
    )

    if not candidates:
        print(
            "ℹ️ No new eligible photos or videos found."
        )
        return

    state = load_state()

    posted_ids = set(
        state.get("posted_file_ids", [])
    )

    candidates = [
        item for item in candidates
        if item["file"]["id"] not in posted_ids
    ]

    if not candidates:
        print(
            "ℹ️ No new eligible media remains."
        )
        return

    selected = random.choice(candidates)

    file_info = selected["file"]
    category_name = selected["category"]
    file_id = file_info["id"]
    filename = file_info["name"]

    print(f"🎲 Selected category: {category_name}")
    print(f"📁 Selected file: {filename}")

    print("⬇️ Downloading media...")

    media_data = download_file(
        drive,
        file_id
    )

    print(
        f"✅ Download complete: {len(media_data)} bytes"
    )

    print("🔐 Connecting to Bluesky...")
    client = connect_bluesky()

    print("🚀 Publishing to RedheadHeaven...")

    # Bij een fout blijft het originele bestand in Ready staan.
    response = publish_media(
        client,
        media_data,
        filename
    )

    if not response or not getattr(response, "uri", None):
        raise RuntimeError(
            "Bluesky did not confirm the post. "
            "Original Drive file has not been moved."
        )

    posted_at = datetime.now(timezone.utc).isoformat()

    # Publicatiegegevens opslaan in Google Drive.
    save_drive_properties(
        drive,
        file_id,
        {
            "redhead_posted_at": posted_at,
            "redhead_post_uri": response.uri,
            "redhead_post_cid": response.cid,
            "redhead_account": TARGET_ACCOUNT,
            "redhead_category": category_name,
            "redhead_media_name": filename
        }
    )

    # Lokale status bijwerken.
    if file_id not in posted_ids:
        state.setdefault(
            "posted_file_ids",
            []
        ).append(file_id)

    state["last_posted_file"] = file_id
    state["last_post_uri"] = response.uri
    state["last_posted_at"] = posted_at

    save_state(state)

    # Alleen na bevestigde publicatie verplaatsen.
    destination_id = destinations[category_name]["ready"]

    move_file(
        drive,
        file_id,
        destination_id
    )

    print("=" * 60)
    print("✅ Katrina post successfully published.")
    print("✅ Google Drive metadata saved.")
    print(
        f"📁 Moved to Hotties/Ready/{category_name}"
    )
    print(f"🔗 {response.uri}")
    print("=" * 60)


if __name__ == "__main__":
    main()
