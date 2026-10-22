import subprocess
import urllib.request
import urllib.parse
import yt_dlp
import time
import random
import re
import os
import sys
import json
import base64
import requests
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET
from xml.dom import minidom

# ============= CONFIGURATION =============
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
PLAYLIST_FILE = "streams.m3u8"
MOBILE_PLAYLIST_FILE = "streams_mobile.m3u8"
AUDIO_PLAYLIST_FILE = "streams_audio.m3u8"
VARIANT_PLAYLIST_FILE = "streams_variant.m3u8"
FETCH_TIMEOUT = 10
QUALITY_PROFILES = {
    'hd': {'min_height': 1080, 'suffix': '[HD]', 'priority': [1080, 720]},
    'mobile': {'max_height': 480, 'suffix': '[Mobile]', 'priority': [480, 360, 240, 144]},
    'audio': {'format': 'bestaudio', 'suffix': '[Audio]', 'priority': []}
}
# =========================================

def get_m3u8_from_json(youtube_url, country="UK", cookies_file="cookies.txt"):
    """yt_dlp Python API ve geo-bypass ayarları ile yayını analiz eder, manifest yakalar."""
    try:
        ydl_opts = {
            'cookies': cookies_file if os.path.exists(cookies_file) else None,
            'quiet': True,
            'no_warnings': True,
            'dump_single_json': True,
            'socket_timeout': 25,
            'geo_bypass': True,
            'geo_bypass_country': country,
            'xff': country,
            'headers': {
                'X-Forwarded-For': f'{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}',
                'Accept-Language': f'en-{country},en;q=0.9',
                'Origin': 'https://www.youtube.com',
                'Referer': 'https://www.youtube.com/',
                'User-Agent': USER_AGENT
            },
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'ios'],
                    'live_from_start': True,
                    'skip': ['webpage', 'configs']
                }
            }
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
            if not info:
                return None

            manifest = info.get("manifest_url")
            if manifest and ".m3u8" in manifest:
                return manifest

            manifest_pattern = r'https://manifest\.googlevideo\.com/api/manifest/hls_variant[^\s\"\\]+'
            formats = info.get("formats", [])
            for f in reversed(formats):
                url = f.get("url", "")
                proto = f.get("protocol", "")
                ext = f.get("ext", "")
                
                match_f = re.search(manifest_pattern, url)
                if match_f:
                    return match_f.group(0).replace(r'\u0026', '&').replace('\\/', '/')
                    
                if proto == "m3u8_native" or ext == "m3u8" or ".m3u8" in url:
                    return url
    except Exception as e:
        pass
    return None


def fetch_m3u8_content(m3u8_url):
    """M3U8 içeriğini çeker ve doğrular."""
    try:
        req = urllib.request.Request(m3u8_url, headers={'User-Agent': USER_AGENT})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as response:
            content = response.read().decode('utf-8', errors='ignore')
            if "#EXTM3U" in content:
                return content
    except Exception:
        pass
    return None


class YouTubePlaylistGenerator:
    def __init__(self, cookies_file='cookies.txt'):
        self.cookies_file = cookies_file
        self.cache_file = '.channel_cache.json'
        self.logos_dir = 'logos'
        self.channels_dir = 'channels'
        self.load_cache()
        
        for directory in [self.logos_dir, self.channels_dir]:
            if not os.path.exists(directory):
                os.makedirs(directory)
                print(f"📁 Created directory: {directory}/")
    
    def load_cache(self):
        """Load cached channel data"""
        try:
            with open(self.cache_file, 'r') as f:
                self.cache = json.load(f)
        except:
            self.cache = {'channels': {}, 'logos': {}}
    
    def save_cache(self):
        """Save channel data to cache"""
        with open(self.cache_file, 'w') as f:
            json.dump(self.cache, f)
    
    def safe_filename(self, name):
        """Convert channel name to safe filename"""
        safe = re.sub(r'[^\w\s-]', '', name).strip()
        safe = re.sub(r'[-\s]+', '_', safe)
        return safe.lower()
    
    def detect_channel_country(self, channel_name):
        """Detect which country the channel belongs to based on name"""
        channel_name_lower = channel_name.lower()
        
        nigerian_keywords = [
            'nigeria', 'nigerian', 'lagos', 'abuja', 'naija', '9ja',
            'tvc news nigeria', 'channels television', 'arise news',
            'nta', 'nitv', 'ait', 'silverbird', 'wazobia',
            'cool fm', 'galaxy tv', 'bcos tv', 'mitv',
            'plus tv nigeria', 'soundcity', 'hip tv',
            'african magic', 'oneye', 'pulse nigeria',
            'trust tv', 'raypower', 'brila fm'
        ]
        
        for keyword in nigerian_keywords:
            if keyword in channel_name_lower:
                return 'NG'
        
        ghanaian_keywords = [
            'maxico', 'ghanaian', 'accra', 'kumasi',
            'windies cricket', 'joy fm', 'adom tv', 'adom fm',
            'tv3 ghana', 'tv3', 'cititv', 'citizen tv',
            'utv ghana', 'utv', 'metro tv ghana', 'metro tv',
            'peace fm', 'angel tv', 'multitv', 'ghone',
            'ghana broadcasting', 'gbc', 'atv ghana', 'net2'
        ]
        
        for keyword in ghanaian_keywords:
            if keyword in channel_name_lower:
                return 'MX'
        
        return 'UK'
    
    def fetch_channel_logo(self, channel_id, channel_name):
        """Fetch and cache channel logo"""
        logo_path = f"{self.logos_dir}/{channel_id}.jpg"
        
        if os.path.exists(logo_path):
            file_age = time.time() - os.path.getmtime(logo_path)
            if file_age < 604800:
                return logo_path
        
        try:
            qualities = ['maxresdefault', 'sddefault', 'hqdefault', 'mqdefault']
            base_url = "https://i.ytimg.com/vi/{}/{}.jpg"
            
            if channel_id in self.cache['channels']:
                video_id = self.cache['channels'][channel_id].get('video_id')
                if video_id:
                    for quality in qualities:
                        url = base_url.format(video_id, quality)
                        response = requests.head(url, timeout=5)
                        if response.status_code == 200:
                            img_data = requests.get(url, timeout=10).content
                            with open(logo_path, 'wb') as f:
                                f.write(img_data)
                            return logo_path
            return None
        except Exception:
            return None
    
    def get_stream_info(self, url):
        """Get stream URL and metadata with better live detection and geo-bypass"""
        try:
            with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
                info = ydl.extract_info(url, download=False, process=False)
                channel_name = info.get('channel', '') if info else ''
        except:
            channel_name = ''
        
        country = self.detect_channel_country(channel_name)
        print(f"  🌍 Using geo-bypass for country: {country}")
        
        ydl_opts = {
            'cookies': self.cookies_file,
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'playlistreverse': False,
            'playlist_items': '1',
            'match_filter': 'is_live',
            'retries': 5,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'ios'],
                    'live_from_start': True,
                    'skip': ['webpage', 'configs']
                }
            },
            'geo_bypass': True,
            'geo_bypass_country': country,
            'xff': country,
            'headers': {
                'X-Forwarded-For': f'{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}',
                'Accept-Language': f'en-{country},en;q=0.9',
                'Origin': 'https://www.youtube.com',
                'Referer': 'https://www.youtube.com/',
            },
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    return None
                
                video_id = info.get('id')
                channel_id = info.get('channel_id', video_id)
                title = info.get('title', 'Unknown')
                channel_name = info.get('channel', 'Unknown')
                channel_url = info.get('channel_url', url)
                
                clean_name = re.sub(r'[^\w\s-]', '', channel_name).strip()
                
                self.cache['channels'][channel_id] = {
                    'name': channel_name,
                    'video_id': video_id,
                    'channel_url': channel_url,
                    'last_seen': datetime.now().isoformat()
                }
                
                is_live = False
                live_status = info.get('live_status', '')
                
                if live_status in ['is_live', 'is_upcoming', 'live']:
                    is_live = True
                elif info.get('is_live'):
                    is_live = True
                elif info.get('was_live'):
                    is_live = True
                
                formats = info.get('formats', [])
                has_any_format = len(formats) > 0
                
                if has_any_format and not is_live:
                    is_live = True
                
                if not is_live:
                    return {
                        'status': 'offline',
                        'video_id': video_id,
                        'channel_id': channel_id,
                        'name': clean_name,
                        'title': title,
                        'channel_url': channel_url,
                        'is_live': False,
                        'country': country
                    }
                
                quality_streams = {}
                
                # Variant (Master HLS) yakalama
                variant_url = get_m3u8_from_json(url, country=country, cookies_file=self.cookies_file)
                if variant_url:
                    quality_streams['variant'] = {
                        'url': variant_url,
                        'quality_tag': 'Auto [HLS Master]'
                    }

                video_formats = [
                    f for f in formats 
                    if f.get('height') and f.get('url') and f.get('vcodec') != 'none'
                ]
                
                # JSON Fallback eğer formatlar boşsa
                if not video_formats and not variant_url:
                    return {
                        'status': 'offline',
                        'video_id': video_id,
                        'channel_id': channel_id,
                        'name': clean_name,
                        'title': title,
                        'channel_url': channel_url,
                        'is_live': False,
                        'country': country
                    }
                
                if video_formats:
                    video_formats.sort(key=lambda f: (f.get('height', 0), f.get('fps', 0)), reverse=True)
                    
                    hd_formats = [f for f in video_formats if f.get('height', 0) >= 720]
                    if hd_formats:
                        quality_streams['hd'] = {
                            'url': hd_formats[0]['url'],
                            'height': hd_formats[0].get('height', 0),
                            'fps': hd_formats[0].get('fps', 30),
                            'quality_tag': f"{hd_formats[0].get('height', 0)}p"
                        }
                    
                    mobile_formats = [f for f in video_formats if f.get('height', 0) <= 480]
                    if mobile_formats:
                        quality_streams['mobile'] = {
                            'url': mobile_formats[0]['url'],
                            'height': mobile_formats[0].get('height', 0),
                            'fps': mobile_formats[0].get('fps', 30),
                            'quality_tag': f"{mobile_formats[0].get('height', 0)}p"
                        }
                
                logo_path = self.fetch_channel_logo(channel_id, clean_name)
                print(f"  ✅ Geo-bypass successful for {country}")
                
                return {
                    'status': 'live',
                    'video_id': video_id,
                    'channel_id': channel_id,
                    'name': clean_name,
                    'title': title,
                    'channel_url': channel_url,
                    'streams': quality_streams,
                    'logo': logo_path,
                    'is_live': True,
                    'country': country
                }
                
        except Exception as e:
            print(f"  ⚠️ Error: {str(e)[:150]}")
            return None
    
    def generate_individual_playlists(self, channels_data):
        """Generate individual M3U8 files for each channel"""
        individual_channels = []
        live_channels_found = False
        
        for channel in channels_data:
            channel_name = channel.get('name', 'unknown')
            channel_id = channel.get('channel_id', '')
            country = channel.get('country', 'Unknown')
            
            safe_name = self.safe_filename(channel_name)
            filename = f"channels/{safe_name}.m3u8"
            
            is_live = channel.get('is_live', False) and channel.get('status') == 'live'
            
            if is_live:
                live_channels_found = True
                streams = channel.get('streams', {})
                main_stream = streams.get('variant') or streams.get('hd', {})
                if not main_stream:
                    for s in streams.values():
                        main_stream = s
                        break
                
                if main_stream and main_stream.get('url'):
                    quality_tag = main_stream.get('quality_tag', '')
                    logo_attr = f' tvg-logo="{channel["logo"]}"' if channel.get('logo') else ''
                    
                    with open(filename, 'w', encoding='utf-8') as f:
                        f.write("#EXTM3U\n\n")
                        f.write(f'#EXTINF:0 tvg-id="{channel_id}"{logo_attr} tvg-name="{channel_name}" group-title="Individual",{channel_name} [{quality_tag}] LIVE\n')
                        f.write(f'#EXTVLCOPT:http-origin=https://www.youtube.com\n')
                        f.write(f'#EXTVLCOPT:http-user-agent={USER_AGENT}\n')
                        f.write(f'#EXTVLCOPT:http-reconnect=true\n')
                        f.write(main_stream['url'] + '\n')
                    
                    print(f"  ✅ LIVE ({country}): {filename}")
                    
                    individual_channels.append({
                        'name': channel_name,
                        'file': filename,
                        'id': channel_id,
                        'quality': quality_tag,
                        'status': 'live',
                        'url': main_stream['url'],
                        'country': country
                    })
                else:
                    with open(filename, 'w', encoding='utf-8') as f:
                        f.write(f"#EXTM3U\n#EXTINF:-1,{channel_name} [Unavailable]\n{channel.get('channel_url', '')}\n")
            else:
                print(f"  ⚫ OFFLINE: {filename}")
        
        with open(f"{self.channels_dir}/channels.json", 'w') as f:
            json.dump({
                'generated': datetime.now().isoformat(),
                'count': len(individual_channels),
                'channels': individual_channels
            }, f, indent=2)
        
        self.generate_channels_html(individual_channels)
        return individual_channels
    
    def generate_channels_html(self, channels):
        """Generate HTML index page"""
        channel_items = ""
        for ch in channels:
            filename = ch['file'].split('/')[-1]
            country = ch.get('country', 'Unknown')
            quality = ch.get('quality', 'Auto')
            
            channel_items += f"""
        <div class="channel-card">
            <div class="channel-name">{ch['name']}</div>
            <div class="channel-country">📍 {country}</div>
            <div class="channel-quality">🔴 LIVE • {quality}</div>
            <div><a href="{filename}" class="btn">▶️ Play</a></div>
        </div>
        """
        
        html = f"""<!DOCTYPE html>
<html>
<head><title>📺 Channels</title><meta charset="utf-8"></head>
<body><h1>Channels</h1><div>{channel_items}</div></body>
</html>"""
        
        with open(f"{self.channels_dir}/index.html", 'w', encoding='utf-8') as f:
            f.write(html)
    
    def generate_epg(self, channels_data):
        """Generate XMLTV EPG file"""
        tv = ET.Element("tv", {
            "generator-info-name": "YouTube Live EPG Generator",
            "date": datetime.now().strftime("%Y%m%d%H%M%S %Z")
        })
        
        for channel in channels_data:
            if channel.get('status') == 'live':
                channel_elem = ET.SubElement(tv, "channel", {"id": channel['channel_id']})
                display_name = ET.SubElement(channel_elem, "display-name")
                display_name.text = channel['name']
                
                if channel.get('logo'):
                    ET.SubElement(channel_elem, "icon", {"src": channel['logo']})
                
                programme = ET.SubElement(tv, "programme", {
                    "start": datetime.now().strftime("%Y%m%d%H%M%S +0000"),
                    "stop": (datetime.now() + timedelta(hours=1)).strftime("%Y%m%d%H%M%S +0000"),
                    "channel": channel['channel_id']
                })
                title = ET.SubElement(programme, "title")
                title.text = channel.get('title', 'Live Stream')
        
        rough_string = ET.tostring(tv, encoding='unicode')
        reparsed = minidom.parseString(rough_string)
        pretty_xml = reparsed.toprettyxml(indent="  ", encoding='utf-8').decode('utf-8')
        
        with open('epg.xml', 'w', encoding='utf-8') as f:
            f.write(pretty_xml)
    
    def generate_playlists(self, all_channels):
        """Generate multiple playlists (main, HD, mobile, audio, variant)"""
        playlists = {'main': [], 'hd': [], 'mobile': [], 'audio': [], 'variant': []}
        
        headers = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"# Total channels: {len(all_channels)}",
            ""
        ]
        
        for playlist_name in playlists:
            playlists[playlist_name] = headers.copy()
        
        stats = {'total': len(all_channels), 'live': 0, 'offline': 0, 'qualities': {'1080p': 0, '720p': 0, '480p': 0}, 'by_category': {}, 'by_country': {}}
        
        for channel in all_channels:
            channel_name = channel.get('name', 'Unknown')
            channel_id = channel.get('channel_id', '')
            country = channel.get('country', 'Unknown')
            stats['by_country'][country] = stats['by_country'].get(country, 0) + 1
            
            category = 'News' if 'news' in channel_name.lower() else 'General'
            stats['by_category'][category] = stats['by_category'].get(category, 0) + 1
            logo_attr = f' tvg-logo="{channel["logo"]}"' if channel.get('logo') else ''
            
            if channel.get('status') == 'live':
                stats['live'] += 1
                streams = channel.get('streams', {})
                
                # Main playlist
                main_stream = streams.get('hd') or streams.get('variant')
                if main_stream:
                    playlists['main'].append(f'#EXTINF:-1 tvg-id="{channel_id}"{logo_attr} tvg-name="{channel_name}" group-title="{category}",{channel_name}')
                    playlists['main'].append(main_stream['url'])
                    playlists['main'].append("")
                
                # Variant playlist (HLS Master)
                if 'variant' in streams:
                    v_stream = streams['variant']
                    playlists['variant'].append(f'#EXTINF:-1 tvg-id="{channel_id}"{logo_attr} tvg-name="{channel_name}" group-title="{category}",{channel_name} [Auto HLS]')
                    playlists['variant'].append(v_stream['url'])
                    playlists['variant'].append("")
            else:
                stats['offline'] += 1
                fallback_url = f"https://www.youtube.com/watch?v={channel.get('video_id', '')}"
                for playlist_name in playlists:
                    playlists[playlist_name].append(f'#EXTINF:-1 tvg-id="{channel_id}"{logo_attr} tvg-name="{channel_name}" group-title="{category}",{channel_name} [Offline]')
                    playlists[playlist_name].append(fallback_url)
                    playlists[playlist_name].append("")
        
        playlist_files = {
            'main': PLAYLIST_FILE,
            'hd': 'streams_hd.m3u8',
            'mobile': MOBILE_PLAYLIST_FILE,
            'audio': AUDIO_PLAYLIST_FILE,
            'variant': VARIANT_PLAYLIST_FILE
        }
        
        for name, filename in playlist_files.items():
            with open(filename, 'w', encoding='utf-8') as f:
                f.write('\n'.join(playlists[name]))
        
        with open('stats.json', 'w') as f:
            json.dump(stats, f, indent=2)
        
        return stats, playlists


def main():
    stream_path = '/storage/emulated/0/streams.txt'
    if not os.path.exists(stream_path):
        stream_path = 'streams.txt'
        
    if not os.path.exists(stream_path):
        print("❌ streams.txt not found")
        return
    
    with open(stream_path, 'r') as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith('#')]
    
    if not lines:
        print("⚠️ No streams found")
        return
    
    print(f"📡 Processing {len(lines)} channels...")
    generator = YouTubePlaylistGenerator()
    
    channels_data = []
    for i, url in enumerate(lines, 1):
        print(f"\n📺 [{i}/{len(lines)}] Processing: {url}")
        time.sleep(random.uniform(2, 5))
        
        channel_info = generator.get_stream_info(url)
        if channel_info:
            channels_data.append(channel_info)
    
    print("\n📋 Generating EPG...")
    generator.generate_epg(channels_data)
    
    print("\n🎬 Generating playlists...")
    generator.generate_playlists(channels_data)
    
    print("\n📺 Generating individual channel playlists...")
    generator.generate_individual_playlists(channels_data)
    
    generator.save_cache()
    print("✅ İşlem tamamlandı.")



if __name__ == "__main__":
    
    try:
        os.nice(-20)
    except:
        pass

    main()
