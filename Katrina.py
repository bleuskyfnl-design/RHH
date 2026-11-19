
import os
import io
import json
import random
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

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

SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_MIME = "application/vnd.google-apps.folder"


# ============================================================
# GOOGLE DRIVE
# ============================================================

def connect_drive():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

    if not raw:
        raise RuntimeError(
            "Missing GitHub Secret: GOOGLE_SERVICE_ACCOUNT_JSON"
        )

    credentials = service_account.Credentials.from_service_account_info(
        json.loads(raw),
        scopes=SCOPES
    )

    return build("drive", "v3", credentials=credentials)


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
        if item["mimeType"] == FOLDER_MIME and item["name"] == name:
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
        category_destinations[category["name"]] = {
            "ready": find_or_create_folder(
                service, category["name"], hotties_ready_id
            ),
            "used": find_or_create_folder(
                service, category["name"], hotties_used_id
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
    password = os.environ.get(PASSWORD_SECRET)

    if not password:
        raise RuntimeError(
            f"Missing GitHub Secret: {PASSWORD_SECRET}"
        )

    client = Client()
    client.login(TARGET_ACCOUNT, password)

    print(f"✅ Logged in as {TARGET_ACCOUNT}")
    return client


# ============================================================
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

    # De zichtbare tekst bevat geen https://
    # De link verwijst wel naar de volledige website-URL.
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

            extension = os.path.splitext(
                file_info["name"].lower()
            )[1]

            if extension not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
                continue

            properties = file_info.get("appProperties", {})

            # Voorkom dat hetzelfde Drive-bestand opnieuw wordt geplaatst.
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
# Geen balken, geen onnodige uitsnede.
# Originele beeldverhouding blijft behouden.
# ============================================================

def prepare_image(data):
    image = Image.open(io.BytesIO(data))

    # Corrigeert foto's die via EXIF gedraaid moeten worden.
    image = ImageOps.exif_transpose(image)
    image.load()

    # Transparante afbeeldingen krijgen een witte achtergrond.
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A"))
        image = background
    else:
        image = image.convert("RGB")

    # Verkleinen zonder de oorspronkelijke verhoudingen te veranderen.
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
            return result

        quality -= 5

        if quality < 45:
            image.thumbnail(
                (1600, 1600),
                Image.Resampling.LANCZOS
            )
            quality = 75

            output = io.BytesIO()
            image.save(
                output,
                format="JPEG",
                quality=quality,
                optimize=True
            )

            result = output.getvalue()

            if len(result) <= MAX_IMAGE_SIZE:
                return result

            raise RuntimeError(
                "Image could not be compressed below 1.9 MB."
            )


# ============================================================
# VIDEO PREPARATION
# FFmpeg wordt alleen gebruikt als een video geselecteerd is.
# ============================================================

def ensure_ffmpeg():
    if shutil.which("ffmpeg"):
        return

    print("📦 Installing FFmpeg...")

    subprocess.run(
        ["sudo", "apt-get", "update", "-y"],
        check=True
    )

    subprocess.run(
        ["sudo", "apt-get", "install", "-y", "ffmpeg"],
        check=True
    )

    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg installation failed.")


def prepare_video(data, filename):
    ensure_ffmpeg()

    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = os.path.join(temp_dir, filename)
        output_path = os.path.join(temp_dir, "katrina_ready.mp4")

        with open(input_path, "wb") as file:
            file.write(data)

        command = [
            "ffmpeg",
            "-y",
            "-i", input_path,
            "-t", str(MAX_VIDEO_SECONDS),
            "-vf",
            (
                "scale="
                "if(gt(iw,ih),min(1920,iw),-2):"
                "if(gt(iw,ih),-2,min(1920,ih))"
            ),
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-sn",
            output_path
        ]

        print("🎬 Converting video for Bluesky...")

        subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        with open(output_path, "rb") as file:
            video_data = file.read()

    if len(video_data) > MAX_VIDEO_SIZE:
        raise RuntimeError(
            "Converted video is larger than 95 MB. "
            "Original Drive file has not been moved."
        )

    return video_data


# ============================================================
# PUBLISHING
# ============================================================

def publish_image(client, media_data):
    caption = build_caption(client)

    return client.send_image(
        text=caption,
        image=media_data,
        image_alt=ALT_TEXT
    )


def publish_video(client, video_data, filename):
    caption = build_caption(client)

    upload_response = client.upload_video(
        video=video_data,
        video_name=os.path.splitext(filename)[0] + ".mp4",
        video_alt=ALT_TEXT
    )

    video_blob = upload_response.blob

    embed = models.AppBskyEmbedVideo.Main(
        video=video_blob,
        alt=ALT_TEXT
    )

    return client.send_post(
        text=caption,
        embed=embed
    )


def publish_media(client, media_data, filename):
    extension = os.path.splitext(filename.lower())[1]

    if extension in IMAGE_EXTENSIONS:
        print("🖼️ Preparing photo...")
        image_data = prepare_image(media_data)

        return publish_image(client, image_data)

    if extension in VIDEO_EXTENSIONS:
        print("🎬 Preparing video...")
        video_data = prepare_video(media_data, filename)

        return publish_video(
            client,
            video_data,
            filename
        )

    raise ValueError(f"Unsupported media: {filename}")


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
        json.dump(state, file, indent=2)


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
        raise RuntimeError("No category folders found in Katrina/Ready.")

    print(f"📂 Found {len(categories)} categories.")

    destinations = ensure_hotties_structure(
        drive,
        categories
    )

    candidates = get_media_candidates(drive, categories)

    if not candidates:
        print("ℹ️ No new eligible photos or videos found.")
        return

    state = load_state()
    posted_ids = set(state.get("posted_file_ids", []))

    # Extra controle via state file.
    candidates = [
        item for item in candidates
        if item["file"]["id"] not in posted_ids
    ]

    if not candidates:
        print("ℹ️ No new eligible media remains.")
        return

    selected = random.choice(candidates)

    file_info = selected["file"]
    category_name = selected["category"]
    file_id = file_info["id"]
    filename = file_info["name"]

    print(f"🎲 Selected category: {category_name}")
    print(f"📁 Selected file: {filename}")

    print("⬇️ Downloading media...")
    media_data = download_file(drive, file_id)

    print("🔐 Connecting to Bluesky...")
    client = connect_bluesky()

    print("🚀 Publishing to RedheadHeaven...")

    # Bij een fout stopt het script hier.
    # Het bestand wordt dan NIET verplaatst.
    response = publish_media(
        client,
        media_data,
        filename
    )

    posted_at = datetime.now(timezone.utc).isoformat()

    # Bewaar publicatiegegevens in Google Drive.
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

    # Bewaar ook de lokale status.
    state.setdefault("posted_file_ids", []).append(file_id)
    state["last_posted_file"] = file_id
    state["last_post_uri"] = response.uri
    state["last_posted_at"] = posted_at

    save_state(state)

    # Verplaats pas nadat Bluesky de publicatie bevestigd heeft.
    destination_id = destinations[category_name]["ready"]

    move_file(
        drive,
        file_id,
        destination_id
    )

    print("=" * 60)
    print("✅ Katrina post successfully published.")
    print("✅ Google Drive metadata saved.")
    print(f"📁 Moved to Hotties/Ready/{category_name}")
    print(f"🔗 {response.uri}")
    print("=" * 60)


if __name__ == "__main__":
    main()
