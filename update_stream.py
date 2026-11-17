#!/usr/bin/env python3

import json
import re
import subprocess
import sys
import requests
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

CONFIG_FILE = "config.json"
COOKIE_FILE = "cookies.txt"

CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
             "AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/148.0.0.0 Safari/537.36")

def load_config() -> Dict:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    if "channels" not in config or not isinstance(config["channels"], list):
        raise ValueError("config.json içinde 'channels' listesi bulunamadı.")

    if not config["channels"]:
        raise ValueError("config.json içinde kanal listesi boş.")

    config.setdefault("quality", "best[height<=1080][fps<=50]/best")
    config.setdefault("output_folder", "playlist")
    config.setdefault("output_playlist", "playerlist.m3u")
    return config

def safe_filename(name: str) -> str:
    replacements = {
        "ç": "c", "Ç": "C",
        "ğ": "g", "Ğ": "G",
        "ı": "i", "İ": "I",
        "ö": "o", "Ö": "O",
        "ş": "s", "Ş": "S",
        "ü": "u", "Ü": "U",
    }
    for old, new in replacements.items():
        name = name.replace(old, new)

    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")
    return f"{name or 'channel'}.m3u"

def is_direct_m3u8(url: str) -> bool:
    clean = url.lower().split("?", 1)[0]
    return clean.endswith(".m3u8")

def normalize_channel_name(name: str) -> str:
    return re.sub(r"[_\-]+", " ", name.lower()).strip()

def is_eurostar_name(name: str) -> bool:
    lower = normalize_channel_name(name)
    return "euro star" in lower or "eurostar" in lower or "star avrupa" in lower

def is_show_turk_name(name: str) -> bool:
    lower = normalize_channel_name(name)
    return "show" in lower and ("türk" in lower or "turk" in lower)

def clean_cookie_file(cookie_file: str = COOKIE_FILE) -> bool:
    path = Path(cookie_file)
    if not path.exists():
        return False
    
    blocked_cookies = ["CONSISTENCY", "ST-sbra4i", "OTZ", "__Secure-YEC", "__Secure-YENID"]
    
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
        lines = content.splitlines()
        
        header_lines = []
        cookie_lines = []
        
        for line in lines:
            if line.startswith("#"):
                header_lines.append(line)
                continue
            if not line.strip():
                continue
            
            parts = line.split("\t")
            if len(parts) >= 7:
                cookie_name = parts[5].strip()
                if cookie_name not in blocked_cookies:
                    cookie_lines.append(line)
        
        cleaned_lines = header_lines + cookie_lines
        
        if not cookie_lines:
            path.write_text("", encoding="utf-8")
            return True
        
        path.write_text("\n".join(cleaned_lines), encoding="utf-8")
        return True
    except Exception as e:
        print(f"   ⚠️ Cookie temizleme hatası: {e}")
        return False

def get_eurostar_token() -> Optional[str]:
    """EuroStar 1080p - HTML'den token çek."""
    headers = {
        "User-Agent": CHROME_UA,
        "Accept": "text/html,application/xhtml+xml",
    }
    page_url = "https://www.eurostartv.com.tr/canli-izle"

    try:
        response = requests.get(page_url, headers=headers, timeout=15)
        pattern = r"var liveUrl = 'https://dygvideo\.dygdigital\.com/live/hls/staravrupa\?token=([a-f0-9]+)';"
        match = re.search(pattern, response.text)

        if not match:
            print("   ❌ EuroStar token HTML'de bulunamadı")
            return None

        token = match.group(1)
        token_url = f"https://dygvideo.dygdigital.com/live/hls/staravrupa?token={token}"

        headers2 = {
            "Origin": "https://www.eurostartv.com.tr",
            "Referer": "https://www.eurostartv.com.tr/",
            "User-Agent": CHROME_UA,
        }

        response2 = requests.get(token_url, headers=headers2, allow_redirects=False, timeout=10)

        if response2.status_code == 302:
            master_url = response2.headers.get("Location")
            if not master_url:
                return None

            match = re.match(r"(.*/)live\.m3u8\?(.*)", master_url)
            if match:
                return f"{match.group(1)}live_1080p3000000kbps/index.m3u8?{match.group(2)}"
            return master_url

        return None
    except Exception as e:
        print(f"   ❌ EuroStar hatası: {e}")
        return None

def get_show_turk_stream() -> str:
    """Show Türk için doğrudan verilen sabit m3u8 adresini döndürür."""
    print("   ✅ Show Türk için doğrudan sabit URL kullanılıyor")
    return "https://ciner.daioncdn.net/showturk/showturk.m3u8?app=web"

def _extract_stream_from_json(info: dict) -> Optional[str]:
    """yt-dlp JSON dump çıktısından (formats/manifest_url) en uygun stream linkini ayıklar."""
    # 1. Doğrudan root üzerinde manifest_url varsa
    if info.get("manifest_url"):
        return info["manifest_url"]

    # 2. Formats listesi içinde arama yap
    formats = info.get("formats", [])
    
    # Önce manifest_url içeren format ara
    for fmt in formats:
        if fmt.get("manifest_url"):
            return fmt["manifest_url"]

    # m3u8 uzantılı format URL'si ara
    for fmt in formats:
        url = fmt.get("url", "")
        if ".m3u8" in url or "manifest" in url:
            return url

    # Hiçbiri bulunamazsa root url veya son format url
    if info.get("url"):
        return info.get("url")

    if formats:
        for fmt in reversed(formats):
            if fmt.get("url"):
                return fmt.get("url")

    return None

def get_youtube_stream_url(youtube_url: str, quality: str) -> Optional[str]:
    """yt-dlp ile -J (JSON dump) kullanarak formats içinden manifest_url / m3u8 bulur."""
    cookie_args: List[str] = []
    cookie_path = Path(COOKIE_FILE)
    
    if cookie_path.exists() and cookie_path.stat().st_size > 0:
        clean_cookie_file(COOKIE_FILE)
        if cookie_path.stat().st_size > 0:
            cookie_args = ["--cookies", COOKIE_FILE]

    common = [
        "yt-dlp",
        "--no-playlist",
        "--no-warnings",
        "--user-agent", CHROME_UA,
        "--referer", "https://www.youtube.com/",
        "--geo-bypass",
        "--socket-timeout", "30",
        "-J",  # JSON Dump isteği
        *cookie_args,
    ]

    attempts = [
        ("deno/ejs/default", [*common, "--js-runtimes", "deno", "--remote-components", "ejs:github", "--extractor-args", "youtube:player_client=default", youtube_url]),
        ("deno/ejs/android", [*common, "--js-runtimes", "deno", "--remote-components", "ejs:github", "--extractor-args", "youtube:player_client=android", youtube_url]),
        ("deno/ejs/web", [*common, "--js-runtimes", "deno", "--remote-components", "ejs:github", "--extractor-args", "youtube:player_client=web", youtube_url]),
        ("classic/default", [*common, "--extractor-args", "youtube:player_client=default", youtube_url]),
        ("classic/android", [*common, "--extractor-args", "youtube:player_client=android", youtube_url]),
    ]

    for label, cmd in attempts:
        print(f"   ▶️ YouTube JSON deneme: {label}")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=50)
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                stream_url = _extract_stream_from_json(data)
                if stream_url:
                    print(f"   ✅ YouTube stream bulundu ({label})")
                    return stream_url
        except Exception as e:
            continue

    print("   ❌ YouTube stream JSON üzerinden alınamadı")
    return None

def get_stream_url(channel: Dict, quality: str) -> Optional[str]:
    channel_name = channel.get("name", "")

    if is_eurostar_name(channel_name):
        print("🔐 EuroStar için token alınıyor...")
        return get_eurostar_token()

    if is_show_turk_name(channel_name):
        print("🔐 Show Türk bağlanıyor...")
        return get_show_turk_stream()

    url = channel.get("url") or channel.get("youtube_url")
    if not url:
        return None

    if is_direct_m3u8(url):
        print("🔗 Direkt m3u8 linki kullanılıyor")
        return url

    print("🎬 YouTube JSON dump ile stream alınıyor...")
    return get_youtube_stream_url(url, quality)

def create_extinf(channel: Dict, stream_url: str) -> str:
    name = channel.get("name", "Unknown")
    return f"#EXTINF:0,{name}\n{stream_url}"

def write_single_channel_file(channel: Dict, stream_url: str, output_folder: Path) -> Path:
    output_folder.mkdir(parents=True, exist_ok=True)

    filename = channel.get("m3u_file") or safe_filename(channel["name"])
    path = output_folder / filename
    name = channel.get("name", "Unknown")

    if is_show_turk_name(name):
        content = (
            "#EXTM3U\n"
            "#EXTVLCOPT:http-referrer=https://www.showturk.com.tr/\n"
            "#EXTINF:-1,Show Türk\n"
            f"{stream_url}\n"
        )
    elif name == "Kanal_Euro_D":
        content = "#EXTM3U\n"
        content += f"#EXTINF:0,{name}\n"
        content += f"{stream_url}\n"
    else:
        content = (
            "#EXTM3U\n"
            "#EXT-X-VERSION:3\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=1280000,RESOLUTION=1280x720\n"
            f"{stream_url}\n"
        )

    path.write_text(content, encoding="utf-8")
    return path

def playlist_display_name(channel: Dict) -> str:
    filename = channel.get("m3u_file") or safe_filename(channel.get("name", "channel"))
    return re.sub(r"\.m3u8?$", "", filename, flags=re.IGNORECASE)

def write_main_playlist(channels: List[Dict], output_folder: Path, output_playlist: str) -> Path:
    output_folder.mkdir(parents=True, exist_ok=True)
    github_base = "https://raw.githubusercontent.com/Tahir2020/TR_Avrupa/refs/heads/main/playlist"

    lines = ["#EXTM3U"]

    for channel in channels:
        filename = channel.get("m3u_file") or safe_filename(channel.get("name", "channel"))
        display_name = playlist_display_name(channel)

        lines.append(f"#EXTINF:0,{display_name}")

        if is_show_turk_name(channel.get("name", "")) and channel.get("_stream_url"):
            lines.append(channel["_stream_url"])
        else:
            lines.append(f"{github_base}/{filename}")

    content = "\n".join(lines) + "\n"
    path = output_folder / output_playlist
    path.write_text(content, encoding="utf-8")

    aliases = {"playerlist.m3u", "playlist.m3u8"}
    aliases.discard(output_playlist)

    for alias in sorted(aliases):
        (output_folder / alias).write_text(content, encoding="utf-8")

    return path

def main() -> int:
    print("=" * 60)
    print("🎬 TV Kanalları M3U Güncelleyici (JSON Dump + Sabit Show Türk)")
    print("=" * 60)
    print(f"🕐 Başlangıç: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    try:
        config = load_config()
    except Exception as e:
        print(f"❌ Config okunamadı: {e}")
        return 1

    quality = config["quality"]
    output_folder = Path(config["output_folder"])
    output_playlist = config["output_playlist"]

    try:
        version = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
        print(f"ℹ️ yt-dlp sürümü: {version}")
    except Exception as e:
        print(f"⚠️ yt-dlp sürümü okunamadı: {e}")

    output_folder.mkdir(parents=True, exist_ok=True)

    for pattern in ("*.m3u", "*.m3u8"):
        for old_file in output_folder.glob(pattern):
            old_file.unlink()

    playlist_entries: List[str] = []
    successful_channels: List[Dict] = []
    failed_channels: List[str] = []

    for index, channel in enumerate(config["channels"], start=1):
        name = channel.get("name", f"Kanal {index}")

        if not (channel.get("url") or channel.get("youtube_url")):
            print(f"\n⚠️ [{index}/{len(config['channels'])}] {name}: url/youtube_url yok, atlandı")
            failed_channels.append(name)
            continue

        print(f"\n🔄 [{index}/{len(config['channels'])}] {name} taranıyor...")
        stream_url = get_stream_url(channel, quality)

        if not stream_url:
            print(f"❌ {name}: Stream URL alınamadı")
            failed_channels.append(name)
            continue

        channel["_stream_url"] = stream_url

        single_file = write_single_channel_file(channel, stream_url, output_folder)
        playlist_entries.append(create_extinf(channel, stream_url))
        successful_channels.append(channel)
        print(f"✅ {name}: {single_file} oluşturuldu")

    if successful_channels:
        main_playlist = write_main_playlist(successful_channels, output_folder, output_playlist)
        print(f"\n✅ Toplu liste oluşturuldu: {main_playlist}")
        print(f"✅ Başarılı kanal sayısı: {len(successful_channels)}/{len(config['channels'])}")
    else:
        print("\n❌ Hiçbir kanal için link alınamadı")
        return 1

    if failed_channels:
        print("\n⚠️ Alınamayan kanallar:")
        for channel_name in failed_channels:
            print(f"   - {channel_name}")

    print(f"\n✅ İşlem tamamlandı: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
