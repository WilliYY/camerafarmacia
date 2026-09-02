import sys
import subprocess

# 0. Verificação da dependência de interface (Pillow).
# A instalação automática por pip transforma a inicialização do NVR em uma
# atualização de código não auditada; a dependência deve existir antes do uso.
try:
    from PIL import Image, ImageTk
except ImportError as error:
    import ctypes
    ctypes.windll.user32.MessageBoxW(
        0,
        "A biblioteca de imagens Pillow não está instalada.\n\n"
        "Instale-a manualmente no ambiente do NVR antes de iniciar o sistema.\n\n"
        f"Detalhe: {error}",
        "Dependência ausente - NVR",
        0x10 | 0x0,
    )
    sys.exit(1)

from wimi_analytics.frame_integrity import FrameIntegrityGuard, assess_frame_integrity
from wimi_analytics.overlay import render_identity_overlay
from wimi_analytics.camera_control import (
    LiveAudioPlayer,
    TUYA_ENDPOINTS,
    TuyaCloudClient,
    TuyaPtzPulseController,
    build_tuya_cloud_config,
    extract_tuya_device_id,
    get_go2rtc_stream_capabilities,
    infer_tuya_endpoint,
    load_tuya_cloud_credentials,
    normalize_tuya_cloud_config,
)

import tkinter as tk
from tkinter import ttk, messagebox
import os
import socket
import urllib.error
import urllib.request
import urllib.parse
import json
import threading
import queue
import time
import ctypes
import shutil
import re
import secrets
import hashlib
from datetime import datetime, timedelta
import io
import zipfile


STREAM_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
STORAGE_FOLDER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$")
ALLOWED_STREAM_PREFIXES = (
    "tuya://",
    "rtsp://",
    "rtsps://",
    "http://",
    "https://",
)

# Binários conhecidos e testados nesta versão. Uma divergência não é
# automaticamente substituída: o bootstrap baixa novamente e só aceita o
# arquivo cujo SHA-256 corresponde à versão fixada.
TRUSTED_BINARY_HASHES = {
    "go2rtc.exe": "923d57252e8139a69c52e4acc1e399a640244a8ef457fd9b7267a25847d68f8c",
    "ffmpeg.exe": "1326dde4c84ff1f96fe6b8916c5bed29e163e9b5dccf995f6f3db069d143ec5e",
}
TRUSTED_VIEWER_ASSET_HASHES = {
    "video-rtc.js": "d48ce627baf7c341a92c0f5844a3c546431f9db873ff21489671aba2ecfe64fb",
}
MAX_GO2RTC_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_GO2RTC_BINARY_BYTES = 64 * 1024 * 1024
MAX_FFMPEG_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_FFMPEG_BINARY_BYTES = 256 * 1024 * 1024


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text_file_normalized(path):
    with open(path, "rb") as file_obj:
        content = file_obj.read()
    return hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest()


def binary_is_trusted(path, expected_hash):
    try:
        return os.path.isfile(path) and sha256_file(path) == expected_hash
    except Exception:
        return False


def require_trusted_binary(path, filename):
    expected_hash = TRUSTED_BINARY_HASHES[filename]
    if not binary_is_trusted(path, expected_hash):
        raise Exception(f"{filename} não corresponde ao binário aprovado para esta versão.")


def generate_web_auth():
    return {
        "username": "viewer",
        "password": secrets.token_urlsafe(24),
    }


def normalize_web_auth(value):
    if not isinstance(value, dict):
        return generate_web_auth()
    username = value.get("username")
    password = value.get("password")
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_-]{3,32}", username):
        username = "viewer"
    if not isinstance(password, str) or len(password) < 16 or any(char in password for char in "\r\n\x00"):
        password = secrets.token_urlsafe(24)
    return {"username": username, "password": password}


def normalize_streams_config(streams):
    normalized = {}
    if not isinstance(streams, dict):
        return normalized
    for raw_name, raw_url in streams.items():
        if not isinstance(raw_name, str) or not STREAM_NAME_PATTERN.fullmatch(raw_name):
            continue
        if not isinstance(raw_url, str):
            continue
        url = raw_url.strip()
        if any(char in url for char in "\r\n\x00"):
            continue
        if not url.lower().startswith(ALLOWED_STREAM_PREFIXES):
            continue
        normalized[raw_name] = url
    return normalized


def normalize_storage_folder_map(value, stream_names):
    normalized = {}
    used_folders = set()
    if isinstance(value, dict):
        for stream_name in stream_names:
            folder_name = value.get(stream_name)
            if (
                isinstance(folder_name, str)
                and STORAGE_FOLDER_PATTERN.fullmatch(folder_name)
                and folder_name not in (".", "..")
                and os.path.basename(folder_name) == folder_name
                and folder_name.casefold() not in used_folders
            ):
                normalized[stream_name] = folder_name
                used_folders.add(folder_name.casefold())

    for index, stream_name in enumerate(stream_names):
        if stream_name in normalized:
            continue
        candidate_number = index + 1
        while True:
            candidate = f"camera {candidate_number}"
            if candidate.casefold() not in used_folders:
                normalized[stream_name] = candidate
                used_folders.add(candidate.casefold())
                break
            candidate_number += 1
    return normalized


def calculate_local_storage_reserve_bytes(total_bytes, configured_gb=None):
    gib = 1024 ** 3
    if configured_gb is not None:
        try:
            configured = int(configured_gb)
        except (TypeError, ValueError):
            configured = 0
        if 10 <= configured <= 500:
            return configured * gib
    return max(20 * gib, int(max(0, total_bytes) * 0.10))


def download_url_to_file_bounded(
    url,
    destination,
    max_bytes,
    timeout,
    progress_callback=None,
):
    parsed_url = urllib.parse.urlparse(url)
    if parsed_url.scheme != "https" or not parsed_url.hostname:
        raise ValueError("download permitido somente por HTTPS")
    parent_dir = os.path.dirname(os.path.abspath(destination))
    os.makedirs(parent_dir, exist_ok=True)
    total_bytes, _, free_bytes = shutil.disk_usage(parent_dir)
    reserve_bytes = calculate_local_storage_reserve_bytes(
        total_bytes,
        (globals().get("CONFIG") or {}).get("local_storage_reserve_gb"),
    )
    if free_bytes < reserve_bytes + max_bytes:
        raise OSError("espaco local insuficiente para download seguro")

    request = urllib.request.Request(url, headers={"User-Agent": "NVR-Camera-Farmacia"})
    downloaded = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final_url = getattr(response, "geturl", lambda: url)()
            if urllib.parse.urlparse(final_url).scheme != "https":
                raise ValueError("redirecionamento de download fora de HTTPS")
            content_length = response.info().get("Content-Length")
            if content_length:
                try:
                    declared_size = int(content_length)
                except (TypeError, ValueError):
                    raise ValueError("Content-Length invalido")
                if declared_size < 0 or declared_size > max_bytes:
                    raise ValueError("download excede o limite de tamanho")

            with open(destination, "wb") as file_obj:
                while True:
                    chunk = response.read(128 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        raise ValueError("download excede o limite de tamanho")
                    file_obj.write(chunk)
                    if progress_callback:
                        progress_callback(downloaded, int(content_length or 0))
                file_obj.flush()
                os.fsync(file_obj.fileno())
        return downloaded
    except Exception:
        try:
            if os.path.exists(destination):
                os.remove(destination)
        except Exception:
            pass
        raise


def extract_zip_member_bounded(zip_path, member_name, destination, max_bytes):
    temporary = destination + ".extracting"
    try:
        with zipfile.ZipFile(zip_path) as archive:
            member = archive.getinfo(member_name)
            if member.is_dir() or member.file_size > max_bytes:
                raise ValueError("arquivo extraido excede o limite de tamanho")
            written = 0
            with archive.open(member) as source_file, open(temporary, "wb") as dest_file:
                while True:
                    chunk = source_file.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValueError("arquivo extraido excede o limite de tamanho")
                    dest_file.write(chunk)
                dest_file.flush()
                os.fsync(dest_file.fileno())
        os.replace(temporary, destination)
        return written
    finally:
        try:
            if os.path.exists(temporary):
                os.remove(temporary)
        except Exception:
            pass


def filter_kernel_144_dump_stamps(stamps, now_datetime, hours=24):
    cutoff = now_datetime - timedelta(hours=max(1, int(hours)))
    future_limit = now_datetime + timedelta(minutes=5)
    accepted = set()
    for raw_stamp in stamps or []:
        if not isinstance(raw_stamp, str):
            continue
        stamp = raw_stamp.strip()
        stamp_format = "%Y%m%d-%H%M%S" if re.fullmatch(r"\d{8}-\d{6}", stamp) else "%Y%m%d-%H%M"
        try:
            occurred_at = datetime.strptime(stamp, stamp_format)
        except ValueError:
            continue
        if cutoff <= occurred_at <= future_limit:
            accepted.add(stamp)
    return sorted(accepted)


def sync_public_viewer(proj_dir):
    viewer_source = os.path.join(proj_dir, "sistema", "visualizador.html")
    player_source = os.path.join(proj_dir, "sistema", "viewer_assets", "video-rtc.js")
    public_dir = os.path.join(proj_dir, "sistema", "web")
    if not os.path.isfile(viewer_source) or not os.path.isfile(player_source):
        return False
    if (
        sha256_text_file_normalized(player_source)
        != TRUSTED_VIEWER_ASSET_HASHES["video-rtc.js"]
    ):
        return False

    os.makedirs(public_dir, exist_ok=True)
    publications = (
        (player_source, os.path.join(public_dir, "video-rtc.js")),
        (viewer_source, os.path.join(public_dir, "visualizador.html")),
        (viewer_source, os.path.join(public_dir, "index.html")),
    )
    staged = []
    try:
        for source, destination in publications:
            temporary = destination + ".tmp"
            shutil.copy2(source, temporary)
            staged.append((temporary, destination))
        for temporary, destination in staged:
            os.replace(temporary, destination)
        return True
    except Exception:
        for temporary, _destination in staged:
            try:
                if os.path.exists(temporary):
                    os.remove(temporary)
            except Exception:
                pass
        return False


def get_short_path(long_path):
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        length = ctypes.windll.kernel32.GetShortPathNameW(long_path, buf, 1024)
        if length > 0:
            return buf.value
    except Exception:
        pass
    return long_path

def atualizar_go2rtc_yaml(proj_dir):
    yaml_path = os.path.join(proj_dir, "sistema", "go2rtc", "go2rtc.yaml")
    ffmpeg_exe = os.path.join(proj_dir, "sistema", "go2rtc", "ffmpeg.exe")
    ffmpeg_path = get_short_path(ffmpeg_exe)

    global CONFIG
    streams_dict = normalize_streams_config((globals().get("CONFIG") or {}).get("streams"))
    web_auth = normalize_web_auth((globals().get("CONFIG") or {}).get("web_auth"))

    streams_lines = []
    live_lines = []
    mjpeg_lines = []
    for name, url in streams_dict.items():
        streams_lines.append(f"  {json.dumps(name)}: {json.dumps(url)}")
        live_lines.append(f"  {json.dumps(name + '_live')}: {json.dumps(f'ffmpeg:{name}#video=h264#hardware')}")
        # Nem todo driver de video oferece codificador MJPEG por hardware.
        # O painel usa este fluxo apenas sob demanda, portanto o encoder de
        # software preserva a gravacao bruta e evita o loop de reconexao.
        mjpeg_lines.append(f"  {json.dumps(name + '_mjpeg')}: {json.dumps(f'ffmpeg:{name}#video=mjpeg')}")

    streams_block = "\n".join(streams_lines)
    live_block = "\n".join(live_lines)
    mjpeg_block = "\n".join(mjpeg_lines)

    conteudo = f'''app:
  modules: [api, rtsp, webrtc, exec, ffmpeg, mjpeg, mpegts, mp4, hls, tuya]

api:
  listen: ":1984"
  username: {json.dumps(web_auth["username"])}
  password: {json.dumps(web_auth["password"])}
  local_auth: false
  static_dir: "../web"
  allow_paths: [/, /api/streams, /api/stream.ts, /api/stream.mjpeg, /api/ws, /api/webrtc, /hls/]

exec:
  allow_paths: [{json.dumps(ffmpeg_path)}]

rtsp:
  listen: ":8554"
  username: {json.dumps(web_auth["username"])}
  password: {json.dumps(web_auth["password"])}

ffmpeg:
  bin: {json.dumps(ffmpeg_path)}

streams:
  # Câmeras originais (H.265 bruto - Usadas para as gravações em 0% CPU)
{streams_block}

  # Câmeras para Visualização Web (Transcodificadas sob demanda para H.264 com Aceleração de Hardware)
{live_block}

  # Câmeras para Stream MJPEG (Transcodificadas sob demanda para MJPEG com Aceleração de Hardware)
{mjpeg_block}
'''
    try:
        os.makedirs(os.path.dirname(yaml_path), exist_ok=True)
        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write(conteudo)
        sync_public_viewer(proj_dir)
        return True
    except Exception:
        return False

def verificar_e_baixar_dependencias(proj_dir, silent=False):
    go2rtc_dir = os.path.join(proj_dir, "sistema", "go2rtc")
    os.makedirs(go2rtc_dir, exist_ok=True)
    
    go2rtc_exe = os.path.join(go2rtc_dir, "go2rtc.exe")
    ffmpeg_exe = os.path.join(go2rtc_dir, "ffmpeg.exe")
    
    needs_go2rtc = not binary_is_trusted(go2rtc_exe, TRUSTED_BINARY_HASHES["go2rtc.exe"])
    needs_ffmpeg = not binary_is_trusted(ffmpeg_exe, TRUSTED_BINARY_HASHES["ffmpeg.exe"])
    
    if not needs_go2rtc and not needs_ffmpeg:
        return atualizar_go2rtc_yaml(proj_dir)
        
    go2rtc_url = "https://github.com/AlexxIT/go2rtc/releases/download/v1.9.14/go2rtc_win64.zip"
    ffmpeg_url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    
    if silent:
        try:
            if needs_go2rtc:
                download_and_extract_go2rtc_silencioso(go2rtc_url, go2rtc_exe, go2rtc_dir)
            if needs_ffmpeg:
                download_and_extract_ffmpeg_silencioso(ffmpeg_url, ffmpeg_exe, go2rtc_dir)
            require_trusted_binary(go2rtc_exe, "go2rtc.exe")
            require_trusted_binary(ffmpeg_exe, "ffmpeg.exe")
            return atualizar_go2rtc_yaml(proj_dir)
        except Exception:
            return False
    else:
        success = [False]
        error_msg = [""]
        
        import tkinter as tk
        from tkinter import ttk
        
        splash = tk.Tk()
        splash.title("Instalador de Dependências")
        splash.geometry("450x200")
        splash.configure(bg="#0D0E12")
        splash.resizable(False, False)
        
        # Centraliza a janela
        sw = splash.winfo_screenwidth()
        sh = splash.winfo_screenheight()
        x = (sw - 450) // 2
        y = (sh - 200) // 2
        splash.geometry(f"450x200+{x}+{y}")
        
        # Estilos escuros para a barra de progresso
        style = ttk.Style(splash)
        style.theme_use("clam")
        style.configure("Installer.Horizontal.TProgressbar", 
                        troughcolor="#161822", 
                        background="#3B82F6", 
                        bordercolor="#0D0E12", 
                        lightcolor="#3B82F6", 
                        darkcolor="#3B82F6")
        
        lbl_title = tk.Label(splash, text="Configurando NVR Farmácia (Primeiro Uso)", font=("Segoe UI", 12, "bold"), fg="#F3F4F6", bg="#0D0E12")
        lbl_title.pack(pady=(20, 10))
        
        lbl_status = tk.Label(splash, text="Preparando...", font=("Segoe UI", 9), fg="#9CA3AF", bg="#0D0E12")
        lbl_status.pack(pady=5)
        
        prog_var = tk.DoubleVar()
        pb = ttk.Progressbar(splash, variable=prog_var, maximum=100, style="Installer.Horizontal.TProgressbar", length=360)
        pb.pack(pady=10)
        
        lbl_percent = tk.Label(splash, text="0%", font=("Segoe UI", 9, "bold"), fg="#3B82F6", bg="#0D0E12")
        lbl_percent.pack()
        
        def run_installer_thread():
            import zipfile
            try:
                # 1. Download go2rtc
                if needs_go2rtc:
                    update_gui("Baixando Ponte RTSP (go2rtc.exe)...", 0)

                    temp_zip = os.path.join(go2rtc_dir, "go2rtc.zip.tmp")

                    def update_go2rtc_progress(downloaded, total_size):
                        if total_size > 0:
                            pct = int(downloaded * 100 / total_size)
                            update_gui(
                                f"Baixando go2rtc.zip... {pct}% ({downloaded/(1024*1024):.1f}MB)",
                                int(pct * 0.3),
                            )

                    download_url_to_file_bounded(
                        go2rtc_url,
                        temp_zip,
                        MAX_GO2RTC_ARCHIVE_BYTES,
                        timeout=30,
                        progress_callback=update_go2rtc_progress,
                    )
                    update_gui("Extraindo go2rtc.exe...", 28)
                    extract_zip_member_bounded(
                        temp_zip,
                        "go2rtc.exe",
                        go2rtc_exe,
                        MAX_GO2RTC_BINARY_BYTES,
                    )
                    require_trusted_binary(go2rtc_exe, "go2rtc.exe")
                    os.remove(temp_zip)
                
                # 2. Download ffmpeg
                if needs_ffmpeg:
                    update_gui("Baixando Transcodificador (ffmpeg.exe - ZIP)...", 30)

                    temp_zip = os.path.join(go2rtc_dir, "ffmpeg.zip.tmp")

                    def update_ffmpeg_progress(downloaded, total_size):
                        if total_size > 0:
                            pct = int(downloaded * 100 / total_size)
                            update_gui(
                                f"Baixando ffmpeg.zip... {pct}% ({downloaded/(1024*1024):.1f}MB)",
                                30 + int(pct * 0.6),
                            )

                    download_url_to_file_bounded(
                        ffmpeg_url,
                        temp_zip,
                        MAX_FFMPEG_ARCHIVE_BYTES,
                        timeout=45,
                        progress_callback=update_ffmpeg_progress,
                    )
                    update_gui("Extraindo ffmpeg.exe do arquivo ZIP...", 92)
                    with zipfile.ZipFile(temp_zip) as archive:
                        ffmpeg_path_in_zip = next(
                            (
                                name for name in archive.namelist()
                                if name.replace("\\", "/").endswith("/ffmpeg.exe")
                                or name == "ffmpeg.exe"
                            ),
                            None,
                        )
                    if not ffmpeg_path_in_zip:
                        raise Exception("ffmpeg.exe não foi encontrado no arquivo ZIP.")
                    extract_zip_member_bounded(
                        temp_zip,
                        ffmpeg_path_in_zip,
                        ffmpeg_exe,
                        MAX_FFMPEG_BINARY_BYTES,
                    )
                    require_trusted_binary(ffmpeg_exe, "ffmpeg.exe")
                    os.remove(temp_zip)
                                    
                update_gui("Configurando rotas de vídeo e caminhos...", 98)
                if not atualizar_go2rtc_yaml(proj_dir):
                    raise Exception("Não foi possível gerar a configuração segura do go2rtc.")
                update_gui("Instalação concluída com sucesso!", 100)
                time.sleep(1.0)
                success[0] = True
                splash.after(0, splash.destroy)
                
            except Exception as e:
                error_msg[0] = str(e)
                cleanup_files = [
                    os.path.join(go2rtc_dir, "go2rtc.zip.tmp"),
                    os.path.join(go2rtc_dir, "ffmpeg.zip.tmp"),
                ]
                if needs_go2rtc:
                    cleanup_files.append(go2rtc_exe)
                if needs_ffmpeg:
                    cleanup_files.append(ffmpeg_exe)
                for f in cleanup_files:
                    if os.path.exists(f):
                        try:
                            os.remove(f)
                        except Exception:
                            pass
                splash.after(0, splash.destroy)
                
        def update_gui(text, value):
            splash.after(0, lambda: lbl_status.configure(text=text))
            splash.after(0, lambda: prog_var.set(value))
            splash.after(0, lambda: lbl_percent.configure(text=f"{int(value)}%"))
            
        threading.Thread(target=run_installer_thread, daemon=True).start()
        splash.mainloop()
        
        if not success[0]:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                f"Erro ao baixar/extrair dependências necessárias:\n\n{error_msg[0]}\n\n"
                "O painel não pode ser iniciado sem estes arquivos.",
                "Erro de Inicialização",
                0x10 | 0x0
            )
            sys.exit(1)
            
        return True

def download_and_extract_go2rtc_silencioso(url, dest_path, go2rtc_dir):
    temp_zip = os.path.join(go2rtc_dir, "go2rtc.zip.tmp")
    download_url_to_file_bounded(
        url,
        temp_zip,
        MAX_GO2RTC_ARCHIVE_BYTES,
        timeout=30,
    )
    extract_zip_member_bounded(
        temp_zip,
        "go2rtc.exe",
        dest_path,
        MAX_GO2RTC_BINARY_BYTES,
    )
    require_trusted_binary(dest_path, "go2rtc.exe")
    os.remove(temp_zip)

def download_and_extract_ffmpeg_silencioso(url, dest_path, go2rtc_dir):
    temp_zip = os.path.join(go2rtc_dir, "ffmpeg.zip.tmp")
    download_url_to_file_bounded(
        url,
        temp_zip,
        MAX_FFMPEG_ARCHIVE_BYTES,
        timeout=45,
    )
    with zipfile.ZipFile(temp_zip) as archive:
        ffmpeg_path_in_zip = next(
            (
                name for name in archive.namelist()
                if name.replace("\\", "/").endswith("/ffmpeg.exe")
                or name == "ffmpeg.exe"
            ),
            None,
        )
    if not ffmpeg_path_in_zip:
        raise Exception("ffmpeg.exe não foi encontrado no arquivo ZIP.")
    extract_zip_member_bounded(
        temp_zip,
        ffmpeg_path_in_zip,
        dest_path,
        MAX_FFMPEG_BINARY_BYTES,
    )
    require_trusted_binary(dest_path, "ffmpeg.exe")
    os.remove(temp_zip)

# Estrutura para obter status de energia e bateria do Windows (queda de energia)
class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]

# Versão do Sistema (usada para o auto-update)
VERSION = "4.13"

CAMERA_DATA_FRESH_SECONDS = 20
CAMERA_DATA_OFFLINE_SECONDS = 90
CAMERA_SIGNAL_OFFLINE_SAMPLES = 10
CAMERA_SIGNAL_RECOVERY_SAMPLES = 2
CAMERA_SIGNAL_COUNTER_CAP = 120


def producer_has_media_evidence(producer):
    if not isinstance(producer, dict) or producer.get("error"):
        return False
    if producer.get("active") is True:
        return True
    for key in ("medias", "tracks", "receivers", "mediainfo"):
        value = producer.get(key)
        if value:
            return True
    return False


def classify_camera_connectivity(
    go2rtc_ok,
    producer_active,
    recording_active=False,
    recording_started_at=None,
    last_data_at=None,
    preview_last_frame_at=None,
    preview_active=False,
    reconnect_failures=0,
    missing_samples=0,
    success_samples=0,
    previous_status=None,
    now=None,
):
    now = time.time() if now is None else now
    recording_age = max(0.0, now - recording_started_at) if recording_started_at else 0.0
    last_data_age = max(0.0, now - last_data_at) if last_data_at else None
    preview_age = max(0.0, now - preview_last_frame_at) if preview_last_frame_at else None
    recorder_fresh = last_data_age is not None and last_data_age <= CAMERA_DATA_FRESH_SECONDS
    preview_fresh = preview_age is not None and preview_age <= CAMERA_DATA_FRESH_SECONDS

    if not go2rtc_ok:
        status, reason = "offline", "go2rtc_offline"
    elif preview_fresh:
        status, reason = "online", "preview_frame_recent"
    elif recording_active:
        if recorder_fresh:
            status, reason = "online", "recording_data_recent"
        elif reconnect_failures >= 3 and recording_age >= 30:
            status, reason = "offline", "recording_reconnect_failures"
        elif last_data_age is None and recording_age < 30:
            status, reason = "connecting", "recording_start_grace"
        elif last_data_age is None and recording_age < CAMERA_DATA_OFFLINE_SECONDS:
            status, reason = "reconnecting", "recording_waiting_first_data"
        elif last_data_age is None:
            status, reason = "offline", "recording_without_data"
        elif last_data_age < CAMERA_DATA_OFFLINE_SECONDS:
            status, reason = "reconnecting", "recording_data_stale"
        else:
            status, reason = "offline", "recording_data_timeout"
    elif producer_active:
        status, reason = "online", "go2rtc_media_active"
    elif not preview_active:
        status, reason = "standby", "no_active_media_probe"
    elif missing_samples >= CAMERA_SIGNAL_OFFLINE_SAMPLES:
        status, reason = "offline", "producer_without_media"
    elif missing_samples >= 3:
        status, reason = "reconnecting", "producer_reconnecting"
    else:
        status, reason = "connecting", "producer_start_grace"

    if (
        status == "online"
        and previous_status == "offline"
        and success_samples < CAMERA_SIGNAL_RECOVERY_SAMPLES
    ):
        status, reason = "reconnecting", "recovery_confirmation"

    return {
        "status": status,
        "reason": reason,
        "last_data_age_seconds": round(last_data_age, 1) if last_data_age is not None else None,
        "preview_age_seconds": round(preview_age, 1) if preview_age is not None else None,
    }


def enrich_camera_connectivity_state(previous, current, now=None):
    now = time.time() if now is None else now
    previous = previous or {}
    result = dict(current)
    previous_status = previous.get("status")
    current_status = result.get("status")

    if previous_status == current_status and previous.get("status_since") is not None:
        result["status_since"] = previous["status_since"]
    else:
        result["status_since"] = now

    last_recovered_at = previous.get("last_recovered_at")
    if current_status == "online" and previous_status != "online":
        last_recovered_at = now
    if last_recovered_at is not None:
        result["last_recovered_at"] = last_recovered_at
    return result


def update_camera_signal_samples(samples, positive_sample, observation_active):
    if positive_sample:
        samples["success"] = min(
            CAMERA_SIGNAL_COUNTER_CAP,
            samples.get("success", 0) + 1,
        )
        samples["missing"] = 0
    elif not observation_active:
        samples["missing"] = 0
        samples["success"] = 0
    else:
        samples["missing"] = min(
            CAMERA_SIGNAL_COUNTER_CAP,
            samples.get("missing", 0) + 1,
        )
        samples["success"] = 0
    return samples


def format_elapsed_short(seconds):
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h" if minutes == 0 else f"{hours}h {minutes}min"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    return f"{days}d" if hours == 0 else f"{days}d {hours}h"


def format_camera_activity(state, now=None):
    now = time.time() if now is None else now
    connectivity = state.get("connectivity") or {}
    status = camera_connectivity_status_from_state(state)
    status_since = connectivity.get("status_since")
    status_age = None if status_since is None else max(0.0, now - status_since)
    age_text = format_elapsed_short(status_age) if status_age is not None else None

    if status == "online":
        last_data_age = connectivity.get("last_data_age_seconds")
        if last_data_age is None:
            return "Sinal de mídia confirmado"
        if last_data_age <= 5:
            return "Mídia recebida agora"
        return f"Última mídia há {format_elapsed_short(last_data_age)}"
    if status == "connecting":
        return "Conectando · tentativa automática ativa"
    if status == "reconnecting":
        prefix = f"Reconectando há {age_text}" if age_text else "Reconectando"
        return f"{prefix} · tentativa automática ativa"
    if status == "standby":
        return "Em espera · sem medição ativa"
    prefix = f"Sem mídia há {age_text}" if age_text else "Sem mídia"
    return f"{prefix} · reconexão automática ativa"


def next_recording_retry_delay(
    current_delay,
    received_data=False,
    base_delay=2.0,
    max_delay=15.0,
):
    if received_data:
        return float(base_delay)
    current_delay = max(float(base_delay), float(current_delay or base_delay))
    return min(float(max_delay), current_delay * 2)


def format_health_collection_time(generated_at):
    try:
        return datetime.fromisoformat(str(generated_at)).strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return "horário indisponível"


def camera_connectivity_status_from_state(state):
    status = (state.get("connectivity") or {}).get("status")
    if status:
        return status
    signal = state.get("signal", "")
    if "Sinal OK" in signal:
        return "online"
    if "Conectando" in signal:
        return "reconnecting"
    return "offline"


def summarize_recording_coverage(cam_states):
    states = list(cam_states.values())
    total = len(states)
    active_count = sum(1 for state in states if state.get("grav_ok"))
    verified_count = sum(
        1 for state in states
        if state.get("grav_ok") and camera_connectivity_status_from_state(state) == "online"
    )

    if active_count == 0:
        label, level = "NVR STATUS: PARADO", "error"
    elif total > 0 and verified_count == total:
        label, level = f"NVR: GRAVANDO {verified_count}/{total}", "ok"
    elif verified_count > 0:
        label, level = f"NVR: GRAVANDO {verified_count}/{total}", "warning"
    else:
        label, level = f"NVR: SEM DADOS 0/{total}", "error"

    return {
        "label": label,
        "level": level,
        "active_count": active_count,
        "verified_count": verified_count,
        "total": total,
    }


def camera_recording_display(state):
    if not state.get("grav_ok"):
        if state.get("duplicate_error"):
            return "DUPLICADO (AVISO)", "warning"
        return "PARADO", "error"

    status = camera_connectivity_status_from_state(state)
    if status == "online":
        return "GRAVANDO", "ok"
    if status == "connecting":
        return "CONECTANDO", "warning"
    if status == "reconnecting":
        return "RECONECTANDO", "warning"
    if status == "standby":
        return "AGUARDANDO DADOS", "warning"
    return "SEM DADOS", "error"


def active_camera_connectivity_issues(active_streams, connectivity_states):
    issues = []
    for stream in active_streams:
        state = connectivity_states.get(stream) or {}
        if state.get("status") != "offline":
            continue
        reason = state.get("reason") or "sem evidencia detalhada"
        issues.append({
            "code": "CAMERA_OFFLINE",
            "severity": "critical",
            "summary": f"A camera {stream.upper()} esta ativa, mas ficou offline.",
            "evidence": f"Estado de conectividade: {reason}.",
            "action": "Verificar energia, rede e disponibilidade da camera sem interromper as demais gravacoes.",
            "stream": stream,
        })
    return issues


def read_log_tail_lines(log_file_path, max_bytes=16 * 1024):
    max_bytes = max(1024, int(max_bytes))
    with open(log_file_path, "rb") as log_file:
        log_file.seek(0, os.SEEK_END)
        file_size = log_file.tell()
        start = max(0, file_size - max_bytes)
        log_file.seek(start)
        data = log_file.read(max_bytes)

    if start:
        newline_pos = data.find(b"\n")
        data = data[newline_pos + 1:] if newline_pos >= 0 else b""
    return data.decode("utf-8", errors="replace").splitlines()


def prune_log_dedup_state(last_logged, suppressed_counts, now, max_entries=500, max_age_seconds=7200):
    if len(last_logged) <= max_entries:
        return

    cutoff = now - max_age_seconds
    for key, timestamp in list(last_logged.items()):
        if timestamp < cutoff:
            last_logged.pop(key, None)
            suppressed_counts.pop(key, None)

    overflow = len(last_logged) - max_entries
    if overflow > 0:
        oldest_keys = sorted(last_logged, key=last_logged.get)[:overflow]
        for key in oldest_keys:
            last_logged.pop(key, None)
            suppressed_counts.pop(key, None)

    for key in list(suppressed_counts):
        if key not in last_logged:
            suppressed_counts.pop(key, None)

# Configurações do Projeto
PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
GO2RTC_EXE = os.path.join(PROJ_DIR, "sistema", "go2rtc", "go2rtc.exe")
FFMPEG_EXE = os.path.join(PROJ_DIR, "sistema", "go2rtc", "ffmpeg.exe")
LOGS_DIR = os.path.join(PROJ_DIR, "sistema", "logs")

# Garante a existência das pastas do projeto
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(os.path.join(PROJ_DIR, "sistema", "backup_gravacoes"), exist_ok=True)
os.makedirs(os.path.join(PROJ_DIR, "sistema", "gravando_temp"), exist_ok=True)

# Arquivo de configuração local
CONFIG_PATH = os.path.join(PROJ_DIR, "sistema", "config.json")

# Limpa o arquivo temporário de update da sessão anterior se existir
# Os arquivos .old sao mantidos como rollback da ultima atualizacao.

def detectar_gdrive_automatico_legacy():
    import ctypes
    import string
    kernel32 = ctypes.windll.kernel32
    # Evita caixas de diálogo do Windows se uma unidade estiver vazia (ex: CD-ROM)
    old_mode = kernel32.SetErrorMode(1)
    try:
        # 1. Tenta buscar pelo Volume Label "FARMACIA"
        for letter in string.ascii_uppercase:
            drive_path = f"{letter}:\\"
            drive_type = kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive_path))
            if drive_type not in (2, 3):
                continue
            volumeNameBuffer = ctypes.create_unicode_buffer(1024)
            rc = kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(drive_path),
                volumeNameBuffer,
                ctypes.sizeof(volumeNameBuffer),
                None, None, None, None, 0
            )
            if rc and volumeNameBuffer.value.upper() == "FARMACIA":
                return os.path.join(drive_path, "farmacia camera")

        # 2. Tenta buscar pela existência física da pasta "\farmacia camera" em qualquer drive removível/fixo
        for letter in string.ascii_uppercase:
            drive_path = f"{letter}:\\"
            drive_type = kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive_path))
            # 2 = DRIVE_REMOVABLE, 3 = DRIVE_FIXED
            if drive_type in (2, 3):
                target_folder = os.path.join(drive_path, "farmacia camera")
                if os.path.exists(target_folder):
                    return target_folder
    except Exception:
        pass
    finally:
        kernel32.SetErrorMode(old_mode)
    
    # Nao escolhe uma unidade arbitraria. Em um PC novo, o destino fica
    # pendente ate encontrar o volume FARMACIA, uma pasta existente ou o
    # usuario configurar explicitamente o caminho.
    return None

CONFIG_LOCK = threading.Lock()

def carregar_config_legacy():
    global CONFIG_LOCK
    with CONFIG_LOCK:
        hd_detectado = detectar_gdrive_automatico()
        hd_padrao = hd_detectado or ""
        
        padrao = {
            "gdrive_root": hd_padrao, 
            "bloco_minutos": 30,
            "streams": {}
        }
        backup_path = CONFIG_PATH + ".bak"
        
        # 1. Se o arquivo principal não existir, tenta restaurar do backup
        if not os.path.exists(CONFIG_PATH):
            if os.path.exists(backup_path):
                try:
                    import shutil
                    shutil.copy2(backup_path, CONFIG_PATH)
                except Exception:
                    pass
            else:
                try:
                    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                        json.dump(padrao, f, indent=4)
                    import shutil
                    shutil.copy2(CONFIG_PATH, backup_path)
                except Exception:
                    pass
                return padrao
                
        # 2. Tenta carregar do arquivo principal
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception:
            # Em caso de corrupção, tenta ler o backup shadow
            try:
                if os.path.exists(backup_path):
                    with open(backup_path, "r", encoding="utf-8") as f:
                        config = json.load(f)
                    import shutil
                    shutil.copy2(backup_path, CONFIG_PATH)
                else:
                    config = padrao.copy()
            except Exception:
                config = padrao.copy()
                
        # 3. Se o SSD estiver disponível, tenta ler e mesclar a configuração compartilhada
        if hd_detectado:
            try:
                shared_path = os.path.join(hd_detectado, "config_compartilhado.json")
                if os.path.exists(shared_path):
                    with open(shared_path, "r", encoding="utf-8") as sf:
                        shared_config = json.load(sf)
                    # Mescla do SSD para a config local
                    for k, v in shared_config.items():
                        config[k] = v
            except Exception:
                pass

        # 4. Valida os campos carregados
        try:
            updated = False
            if "gdrive_root" not in config:
                config["gdrive_root"] = padrao["gdrive_root"]
                updated = True
            elif not os.path.exists(config["gdrive_root"]) and hd_detectado:
                config["gdrive_root"] = hd_detectado
                updated = True
            
            if "bloco_minutos" not in config:
                config["bloco_minutos"] = 30
                updated = True
                
            if "streams" not in config:
                config["streams"] = padrao["streams"]
                updated = True
                
            if updated:
                salvar_config_locked(config)
            return config
        except Exception:
            return padrao

def salvar_config_locked_legacy(config):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
        try:
            import shutil
            shutil.copy2(CONFIG_PATH, CONFIG_PATH + ".bak")
        except Exception:
            pass
        
        # Salva também no SSD se estiver disponível
        gdrive_root = config.get("gdrive_root")
        if gdrive_root and os.path.exists(gdrive_root):
            try:
                shared_path = os.path.join(gdrive_root, "config_compartilhado.json")
                shared_data = {
                    "bloco_minutos": config.get("bloco_minutos", 30),
                    "streams": normalize_streams_config(config.get("streams"))
                }
                with open(shared_path, "w", encoding="utf-8") as sf:
                    json.dump(shared_data, sf, indent=4)
            except Exception:
                pass
    except Exception:
        pass

def salvar_config_legacy(config):
    global CONFIG_LOCK
    with CONFIG_LOCK:
        salvar_config_locked(config)

def get_volume_identity(path):
    drive, _ = os.path.splitdrive(os.path.abspath(path or ""))
    if not drive:
        return None
    try:
        volume_name = ctypes.create_unicode_buffer(1024)
        filesystem_name = ctypes.create_unicode_buffer(1024)
        serial = ctypes.c_ulong()
        maximum_component_length = ctypes.c_ulong()
        filesystem_flags = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(drive + "\\"),
            volume_name,
            len(volume_name),
            ctypes.byref(serial),
            ctypes.byref(maximum_component_length),
            ctypes.byref(filesystem_flags),
            filesystem_name,
            len(filesystem_name),
        )
        if not ok:
            return None
        return {"serial": f"{serial.value:08X}", "label": volume_name.value.upper()}
    except Exception:
        return None


def normalize_storage_identity(value):
    if not isinstance(value, dict):
        return None
    serial = value.get("serial")
    label = value.get("label", "")
    if not isinstance(serial, str) or not re.fullmatch(r"[0-9A-Fa-f]{8}", serial):
        return None
    return {
        "serial": serial.upper(),
        "label": label.upper() if isinstance(label, str) else "",
    }


def storage_path_matches_identity(path, expected_identity):
    if not path:
        return False
    expected = normalize_storage_identity(expected_identity)
    if expected is None:
        return os.path.exists(path)
    observed = get_volume_identity(path)
    return bool(observed and observed.get("serial") == expected["serial"])


def detectar_gdrive_automatico(expected_identity=None):
    import string

    expected = normalize_storage_identity(expected_identity)
    kernel32 = ctypes.windll.kernel32
    old_mode = kernel32.SetErrorMode(1)
    candidates = []
    try:
        for letter in string.ascii_uppercase:
            drive_path = f"{letter}:\\"
            drive_type = kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive_path))
            if drive_type not in (2, 3):
                continue
            identity = get_volume_identity(drive_path)
            target_folder = os.path.join(drive_path, "farmacia camera")
            if expected is not None:
                if identity and identity.get("serial") == expected["serial"]:
                    return target_folder
                continue
            if identity and identity.get("label") == "FARMACIA":
                candidates.append(target_folder)
            elif os.path.exists(target_folder):
                candidates.append(target_folder)
    except Exception:
        return None
    finally:
        kernel32.SetErrorMode(old_mode)
    return candidates[0] if candidates else None


def normalize_trusted_update_hashes(value):
    normalized = {}
    if not isinstance(value, dict):
        return normalized
    for version, hashes in value.items():
        if not isinstance(version, str) or not isinstance(hashes, dict):
            continue
        manager_hash = hashes.get("manager_sha256")
        viewer_hash = hashes.get("viewer_sha256")
        if all(
            isinstance(item, str) and re.fullmatch(r"[0-9A-Fa-f]{64}", item)
            for item in (manager_hash, viewer_hash)
        ):
            normalized[version] = {
                "manager_sha256": manager_hash.lower(),
                "viewer_sha256": viewer_hash.lower(),
            }
    return normalized


def normalize_retention_days(value):
    if isinstance(value, bool):
        return 90
    try:
        days = int(value)
    except (TypeError, ValueError):
        return 90
    return days if 30 <= days <= 3650 else 90


def write_json_atomically(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f"{path}.{os.getpid()}.tmp"
    with open(temporary, "w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, indent=4)
        file_obj.flush()
        try:
            os.fsync(file_obj.fileno())
        except Exception:
            pass
    os.replace(temporary, path)


def load_json_file(path):
    with open(path, "r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def carregar_config():
    with CONFIG_LOCK:
        defaults = {
            "gdrive_root": "",
            "storage_identity": None,
            "bloco_minutos": 30,
            "streams": {},
            "storage_folder_map": {},
            "local_storage_reserve_gb": None,
            "web_auth": generate_web_auth(),
            "trusted_update_hashes": {},
            "retention_days": 90,
            "emergency_cleanup_enabled": False,
            "tuya_cloud": {},
        }
        backup_path = CONFIG_PATH + ".bak"
        config = None
        try:
            config = load_json_file(CONFIG_PATH)
        except Exception:
            try:
                if os.path.exists(backup_path):
                    config = load_json_file(backup_path)
            except Exception:
                config = None
        if not isinstance(config, dict):
            config = {}

        updated = False
        configured_root = config.get("gdrive_root") if isinstance(config.get("gdrive_root"), str) else ""
        configured_identity = normalize_storage_identity(config.get("storage_identity"))
        if configured_root and os.path.exists(configured_root):
            observed_identity = get_volume_identity(configured_root)
            if configured_identity is None and observed_identity:
                configured_identity = observed_identity
                config["storage_identity"] = observed_identity
                updated = True
            elif configured_identity and not storage_path_matches_identity(configured_root, configured_identity):
                configured_root = ""
                config["gdrive_root"] = ""
                updated = True

        detected_root = detectar_gdrive_automatico(configured_identity)
        if detected_root:
            try:
                shared_path = os.path.join(detected_root, "config_compartilhado.json")
                shared_config = load_json_file(shared_path) if os.path.exists(shared_path) else None
                observed_identity = get_volume_identity(detected_root)
                shared_identity = normalize_storage_identity(
                    shared_config.get("storage_identity") if isinstance(shared_config, dict) else None
                )
                if isinstance(shared_config, dict) and (
                    shared_identity is None
                    or (observed_identity and shared_identity["serial"] == observed_identity["serial"])
                ):
                    shared_streams = normalize_streams_config(shared_config.get("streams"))
                    if shared_streams:
                        config["streams"] = shared_streams
                        config["storage_folder_map"] = normalize_storage_folder_map(
                            shared_config.get("storage_folder_map"),
                            list(shared_streams.keys()),
                        )
                        updated = True
                    if shared_config.get("bloco_minutos") in (10, 15, 30):
                        config["bloco_minutos"] = shared_config["bloco_minutos"]
                        updated = True
                    if configured_identity is None and observed_identity:
                        configured_identity = observed_identity
                        config["storage_identity"] = observed_identity
                        updated = True
            except Exception:
                pass

        if not configured_root and detected_root:
            configured_root = detected_root
            config["gdrive_root"] = configured_root
            updated = True
        if configured_root and configured_identity is None:
            observed_identity = get_volume_identity(configured_root)
            if observed_identity:
                config["storage_identity"] = observed_identity
                updated = True
        if config.get("bloco_minutos") not in (10, 15, 30):
            config["bloco_minutos"] = defaults["bloco_minutos"]
            updated = True
        streams = normalize_streams_config(config.get("streams"))
        if config.get("streams") != streams:
            config["streams"] = streams
            updated = True
        storage_folder_map = normalize_storage_folder_map(
            config.get("storage_folder_map"),
            list(streams.keys()),
        )
        if config.get("storage_folder_map") != storage_folder_map:
            config["storage_folder_map"] = storage_folder_map
            updated = True
        configured_reserve = config.get("local_storage_reserve_gb")
        if configured_reserve is not None:
            try:
                configured_reserve = int(configured_reserve)
            except (TypeError, ValueError):
                configured_reserve = None
            if configured_reserve is not None and not 10 <= configured_reserve <= 500:
                configured_reserve = None
        if config.get("local_storage_reserve_gb") != configured_reserve:
            config["local_storage_reserve_gb"] = configured_reserve
            updated = True
        web_auth = normalize_web_auth(config.get("web_auth"))
        if config.get("web_auth") != web_auth:
            config["web_auth"] = web_auth
            updated = True
        trusted_updates = normalize_trusted_update_hashes(config.get("trusted_update_hashes"))
        if config.get("trusted_update_hashes") != trusted_updates:
            config["trusted_update_hashes"] = trusted_updates
            updated = True
        retention_days = normalize_retention_days(config.get("retention_days"))
        if config.get("retention_days") != retention_days:
            config["retention_days"] = retention_days
            updated = True
        emergency_cleanup_enabled = config.get("emergency_cleanup_enabled") is True
        if config.get("emergency_cleanup_enabled") is not emergency_cleanup_enabled:
            config["emergency_cleanup_enabled"] = emergency_cleanup_enabled
            updated = True
        tuya_cloud = normalize_tuya_cloud_config(config.get("tuya_cloud"))
        if config.get("tuya_cloud") != tuya_cloud:
            config["tuya_cloud"] = tuya_cloud
            updated = True
        if "gdrive_root" not in config:
            config["gdrive_root"] = defaults["gdrive_root"]
            updated = True
        if updated or not os.path.exists(CONFIG_PATH):
            salvar_config_locked(config)
        return config


def salvar_config_locked(config):
    try:
        config["streams"] = normalize_streams_config(config.get("streams"))
        config["storage_folder_map"] = normalize_storage_folder_map(
            config.get("storage_folder_map"),
            list(config["streams"].keys()),
        )
        config["web_auth"] = normalize_web_auth(config.get("web_auth"))
        config["storage_identity"] = normalize_storage_identity(config.get("storage_identity"))
        config["trusted_update_hashes"] = normalize_trusted_update_hashes(config.get("trusted_update_hashes"))
        config["retention_days"] = normalize_retention_days(config.get("retention_days"))
        config["emergency_cleanup_enabled"] = config.get("emergency_cleanup_enabled") is True
        config["tuya_cloud"] = normalize_tuya_cloud_config(config.get("tuya_cloud"))
        write_json_atomically(CONFIG_PATH, config)
        shutil.copy2(CONFIG_PATH, CONFIG_PATH + ".bak")

        gdrive_root = config.get("gdrive_root")
        if storage_path_matches_identity(gdrive_root, config.get("storage_identity")):
            shared_path = os.path.join(gdrive_root, "config_compartilhado.json")
            write_json_atomically(shared_path, {
                "bloco_minutos": config.get("bloco_minutos", 30),
                "streams": config["streams"],
                "storage_folder_map": config["storage_folder_map"],
                "storage_identity": config.get("storage_identity"),
            })
        return True
    except Exception:
        return False


def salvar_config(config):
    with CONFIG_LOCK:
        return salvar_config_locked(config)


CONFIG = carregar_config()
GDRIVE_ROOT = CONFIG.get("gdrive_root") or ""

HEALTH_CHECK_INTERVAL_SECONDS = 60
KERNEL_REPORT_CACHE_SECONDS = 5 * 60
STARTUP_LOG_LIMIT = 500


def build_operational_intelligence(snapshot):
    issues = snapshot.get("issues") or []
    metrics = snapshot.get("metrics") or {}
    codes = {issue.get("code") for issue in issues}
    active_streams = set(metrics.get("active_streams") or [])
    no_data_streams = {
        issue.get("stream") for issue in issues
        if issue.get("code") == "STREAM_NO_DATA" and issue.get("stream")
    }

    status = "attention"
    root_cause = "health_issue"
    confidence_score = 70
    headline = "O sistema detectou uma situacao que precisa de revisao."
    explanation = "Os sinais atuais ainda nao formam uma causa unica."
    actions = []
    correlations = []
    heavy_maintenance_allowed = True
    protection_reason = "Nenhum bloqueio de manutencao pesada nesta coleta."
    recording_recommendation = "continue_monitoring"

    if not issues:
        status = "stable"
        root_cause = "no_active_risk"
        confidence_score = 95
        headline = "Sistema estavel nesta coleta."
        explanation = "Gravacao, armazenamento e recursos nao apresentaram alertas ativos."
        actions = ["Manter o monitoramento automatico e a rotina normal de gravacao."]
    elif "SMART_DEGRADED" in codes:
        status = "critical"
        root_cause = "physical_disk_degradation"
        confidence_score = 98
        headline = "Possivel degradacao fisica de disco."
        explanation = "O Windows reportou um disco fora do estado normal."
        actions = [
            "Interromper manutencoes pesadas e preparar copia dos dados importantes.",
            "Executar o diagnostico oficial do fabricante do disco.",
        ]
        correlations = ["SMART_DEGRADED"]
        heavy_maintenance_allowed = False
        protection_reason = "Status basico do Windows nao retornou OK para o disco."
        recording_recommendation = "safe_stop"
    elif "KERNEL_144_NEW_SESSION" in codes and "HD_UNAVAILABLE" in codes:
        status = "critical"
        root_cause = "usb_storage_instability"
        confidence_score = 96
        headline = "Falha USB nova coincidiu com indisponibilidade do HD."
        explanation = "A correlacao aponta primeiro para controlador, cabo, porta ou energia do HD."
        actions = [
            "Encerrar a gravacao com seguranca se o HD nao retornar.",
            "Trocar cabo ou porta USB e revisar a alimentacao do disco.",
            "Nao executar scanner, rotacao manual ou copia pesada ate estabilizar.",
        ]
        correlations = ["KERNEL_144_NEW_SESSION + HD_UNAVAILABLE"]
        heavy_maintenance_allowed = False
        protection_reason = "Falha USB nova e HD indisponivel na mesma sessao."
        recording_recommendation = "safe_stop"
    elif "KERNEL_144_NEW_SESSION" in codes:
        status = "critical"
        root_cause = "usb_controller_instability"
        confidence_score = 92
        headline = "O Windows registrou uma nova falha USB nesta sessao."
        explanation = "Mesmo com o HD acessivel, o controlador ou outro dispositivo USB ficou instavel."
        actions = [
            "Evitar manutencao pesada e acompanhar se o HD permanece acessivel.",
            "Revisar cabo, porta, energia e driver USB antes do teste de 24 horas.",
        ]
        correlations = ["KERNEL_144_NEW_SESSION"]
        heavy_maintenance_allowed = False
        protection_reason = "Novo Kernel_144 detectado nesta sessao."
    elif "POWER_ON_BATTERY" in codes:
        status = "critical" if any(
            issue.get("code") == "POWER_ON_BATTERY" and issue.get("severity") == "critical"
            for issue in issues
        ) else "attention"
        root_cause = "power_instability"
        confidence_score = 95
        headline = "O computador esta operando sem energia AC confirmada."
        explanation = "A gravacao depende da autonomia restante e do encerramento seguro."
        actions = ["Restabelecer a energia e preservar margem para o encerramento seguro."]
        correlations = ["POWER_ON_BATTERY"]
        heavy_maintenance_allowed = False
        protection_reason = "Energia AC indisponivel."
        recording_recommendation = "safe_stop" if status == "critical" else "continue_monitoring"
    elif "LOCAL_SPACE_CRITICAL" in codes and ("BACKUP_PENDING" in codes or "CAMERA_ON_FALLBACK" in codes):
        status = "critical"
        root_cause = "local_fallback_pressure"
        confidence_score = 96
        headline = "O fallback local esta pressionando o disco do Windows."
        explanation = "Backups pendentes e pouco espaco local podem afetar o computador."
        actions = [
            "Restabelecer o HD para sincronizar os backups pendentes.",
            "Nao apagar videos pendentes; liberar apenas arquivos externos ao NVR.",
        ]
        correlations = ["LOCAL_SPACE_CRITICAL + BACKUP_PENDING/FALLBACK"]
        heavy_maintenance_allowed = False
        protection_reason = "Espaco local critico com gravacoes pendentes."
        recording_recommendation = "safe_stop"
    elif "HD_UNAVAILABLE" in codes:
        status = "attention"
        root_cause = "storage_unavailable"
        confidence_score = 92
        headline = "O HD principal esta indisponivel."
        explanation = "O NVR depende do fallback local enquanto o volume nao retorna."
        actions = [
            "Verificar cabo, porta, energia e montagem do volume FARMACIA.",
            "Confirmar que a fila de backup local nao cresce ate o limite do Windows.",
        ]
        correlations = ["HD_UNAVAILABLE"]
        heavy_maintenance_allowed = False
        protection_reason = "HD principal indisponivel."
    elif "GO2RTC_UNAVAILABLE" in codes and no_data_streams:
        status = "critical"
        root_cause = "video_bridge_failure"
        confidence_score = 94
        headline = "A ponte de video parou e as cameras deixaram de entregar dados."
        explanation = "A falha comum esta no go2rtc ou no caminho de rede anterior aos gravadores."
        actions = [
            "Aguardar uma tentativa do watchdog e conferir o log do go2rtc.",
            "Se repetir, verificar rede e acesso das cameras antes de reiniciar o PC.",
        ]
        correlations = ["GO2RTC_UNAVAILABLE + STREAM_NO_DATA"]
    elif "RECORDING_THREAD_DEAD" in codes:
        status = "critical"
        root_cause = "recording_worker_failure"
        confidence_score = 94
        headline = "Uma thread de gravacao parou inesperadamente."
        explanation = "O watchdog deve tentar recuperar, mas a causa precisa ser confirmada no log."
        actions = ["Confirmar a recuperacao da thread e a chegada de novos bytes."]
        correlations = ["RECORDING_THREAD_DEAD"]
    elif active_streams and no_data_streams == active_streams:
        status = "critical"
        root_cause = "upstream_video_outage"
        confidence_score = 86
        headline = "Todas as cameras ativas pararam de entregar dados."
        explanation = "Como a ponte ainda responde, a causa provavel esta na rede, nuvem ou alimentacao das cameras."
        actions = [
            "Verificar internet, roteador e energia das cameras.",
            "Comparar o horario da falha entre as cameras antes de reiniciar servicos.",
        ]
        correlations = ["STREAM_NO_DATA em todas as cameras"]
    elif no_data_streams:
        isolated_stream_is_critical = any(
            issue.get("code") == "STREAM_NO_DATA"
            and issue.get("severity") == "critical"
            and issue.get("stream") in no_data_streams
            for issue in issues
        )
        status = "critical" if isolated_stream_is_critical else "attention"
        root_cause = "single_camera_path"
        confidence_score = 88
        names = ", ".join(sorted(stream.upper() for stream in no_data_streams))
        headline = (
            f"A camera {names} esta offline e sem dados."
            if isolated_stream_is_critical
            else f"A camera {names} esta ativa, mas sem dados recentes."
        )
        explanation = "As outras cameras nao compartilham o sintoma, reduzindo a suspeita sobre o PC e o HD."
        actions = ["Verificar sinal, rede e alimentacao da camera afetada."]
        correlations = ["STREAM_NO_DATA isolado"]
    elif "MEMORY_GROWTH_SUSPECT" in codes or "PROCESS_MEMORY_HIGH" in codes:
        resource_is_critical = any(
            issue.get("severity") == "critical"
            and issue.get("code") in {"MEMORY_GROWTH_SUSPECT", "PROCESS_MEMORY_HIGH"}
            for issue in issues
        )
        status = "critical" if resource_is_critical else "attention"
        root_cause = "process_resource_growth"
        confidence_score = 84
        headline = "A memoria do NVR aumentou acima do esperado na janela observada."
        explanation = "A variacao sugere acumulacao de recursos, streams ou filas na sessao atual."
        actions = ["Acompanhar a tendencia por mais coletas e revisar streams, imagens e filas."]
        correlations = ["MEMORY_GROWTH_SUSPECT/PROCESS_MEMORY_HIGH"]
    elif codes == {"KERNEL_144_REPORTS"}:
        status = "attention"
        root_cause = "usb_history"
        confidence_score = 95
        headline = "Ha historico USB recente, sem falha nova nesta sessao."
        explanation = "O alerta atual descreve eventos anteriores e nao prova piora causada pelo NVR agora."
        actions = [
            "Manter observacao e resolver USBXHCI antes do teste continuo de 24 horas."
        ]
        correlations = ["KERNEL_144_REPORTS historico"]
        heavy_maintenance_allowed = False
        protection_reason = "Historico USB recente ainda nao investigado."
    else:
        candidates = [issue for issue in issues if issue.get("code") != "KERNEL_144_REPORTS"]
        if not candidates:
            candidates = issues
        ranked = sorted(
            candidates,
            key=lambda item: 1 if item.get("severity") == "critical" else 0,
            reverse=True,
        )
        primary = ranked[0]
        status = "critical" if primary.get("severity") == "critical" else "attention"
        root_cause = primary.get("code", "health_issue").lower()
        confidence_score = 72
        headline = primary.get("summary") or headline
        explanation = primary.get("evidence") or explanation
        actions = [primary.get("action")] if primary.get("action") else []
        correlations = sorted(code for code in codes if code)

    if not actions:
        actions = ["Revisar o diagnostico detalhado antes de executar qualquer acao corretiva."]

    critical_resource = any(
        issue.get("severity") == "critical"
        and issue.get("code") in {"MEMORY_GROWTH_SUSPECT", "PROCESS_MEMORY_HIGH"}
        for issue in issues
    )
    if "SMART_DEGRADED" in codes:
        heavy_maintenance_allowed = False
        protection_reason = "Status basico do Windows nao retornou OK para o disco."
        recording_recommendation = "safe_stop"
    elif "KERNEL_144_NEW_SESSION" in codes:
        heavy_maintenance_allowed = False
        protection_reason = "Novo Kernel_144 detectado nesta sessao."
        if "HD_UNAVAILABLE" in codes:
            recording_recommendation = "safe_stop"
    elif "POWER_ON_BATTERY" in codes:
        heavy_maintenance_allowed = False
        protection_reason = "Energia AC indisponivel."
    elif "LOCAL_SPACE_CRITICAL" in codes:
        heavy_maintenance_allowed = False
        protection_reason = "Espaco local critico."
        if "BACKUP_PENDING" in codes or "CAMERA_ON_FALLBACK" in codes:
            recording_recommendation = "safe_stop"
    elif "HD_UNAVAILABLE" in codes:
        heavy_maintenance_allowed = False
        protection_reason = "HD principal indisponivel."
    elif critical_resource:
        heavy_maintenance_allowed = False
        protection_reason = "Recursos do processo em nivel critico."
    elif "KERNEL_144_REPORTS" in codes:
        heavy_maintenance_allowed = False
        protection_reason = "Historico USB recente ainda nao investigado."

    confidence = "high" if confidence_score >= 85 else ("medium" if confidence_score >= 65 else "low")
    return {
        "schema_version": 1,
        "status": status,
        "root_cause": root_cause,
        "confidence": confidence,
        "confidence_score": confidence_score,
        "headline": headline,
        "explanation": explanation,
        "priority_actions": actions[:3],
        "correlations": correlations,
        "evidence_codes": sorted(code for code in codes if code),
        "affected_streams": sorted(stream for stream in no_data_streams if stream),
        "hardware_protection": {
            "heavy_maintenance_allowed": heavy_maintenance_allowed,
            "reason": protection_reason,
            "recording_recommendation": recording_recommendation,
        },
    }


def garantir_limite_backup_local(backup_dir, min_free_bytes=None):
    """Informa se ha reserva local sem apagar gravacoes ainda nao sincronizadas."""
    reserve_bytes = min_free_bytes
    try:
        os.makedirs(backup_dir, exist_ok=True)
        total_bytes, _, free_bytes = shutil.disk_usage(backup_dir)
        if reserve_bytes is None:
            reserve_bytes = calculate_local_storage_reserve_bytes(
                total_bytes,
                (globals().get("CONFIG") or {}).get("local_storage_reserve_gb"),
            )
        return {
            "ok": free_bytes >= reserve_bytes,
            "free_bytes": free_bytes,
            "reserve_bytes": reserve_bytes,
        }
    except Exception:
        if reserve_bytes is None:
            reserve_bytes = 20 * 1024 ** 3
        return {
            "ok": False,
            "free_bytes": 0,
            "reserve_bytes": reserve_bytes,
        }

# Cores do Tema Escuro Premium
BG_COLOR = "#0D0E12"       # Fundo principal cinza escuro azulado
CARD_COLOR = "#161822"     # Cards com contraste leve
ACCENT_COLOR = "#3B82F6"   # Azul moderno (Vibrant Blue)
TEXT_COLOR = "#F3F4F6"     # Texto principal claro
TEXT_MUTED = "#9CA3AF"     # Texto secundário cinza
GREEN_COLOR = "#10B981"    # Verde esmeralda (Ativo)
RED_COLOR = "#EF4444"      # Vermelho coral (Inativo)
ORANGE_COLOR = "#F59E0B"   # Laranja âmbar (Atenção)

BADGE_BG_MAP = {
    GREEN_COLOR: "#064E3B",   # Verde -> Escuro
    RED_COLOR: "#7F1D1D",     # Vermelho -> Escuro
    ORANGE_COLOR: "#78350F",  # Laranja -> Escuro
    "#EF4444": "#7F1D1D",
    "#10B981": "#064E3B",
    "#F59E0B": "#78350F",
    "#60A5FA": "#1E3A8A",
    "#3B82F6": "#1E3A8A"
}

class StatusLED(tk.Canvas):
    """Um pequeno indicador LED circular desenhado via Canvas"""
    def __init__(self, parent, size=12, bg_color=CARD_COLOR):
        super().__init__(parent, width=size, height=size, bg=bg_color, highlightthickness=0)
        self.size = size
        self.led = self.create_oval(2, 2, size-2, size-2, fill=ORANGE_COLOR, outline="#78350F", width=1)
        
    def set_status(self, color, border_color):
        self.itemconfig(self.led, fill=color, outline=border_color)

class LiveCameraWidget(tk.Frame):
    """Widget de visualização de câmera ao vivo embutida, colapsável e com modo tela cheia"""
    def __init__(self, parent, stream_name, app_instance):
        super().__init__(parent, bg=BG_COLOR)
        self.stream_name = stream_name
        self.app = app_instance
        self.expanded = False
        self.thread = None
        self.running = False
        self.continuous_analysis = False
        self.photo = None
        self._display_lock = threading.Lock()
        self._pending_display_image = None
        self._display_update_scheduled = False
        self._frame_integrity_guard = FrameIntegrityGuard()
        self._last_integrity_log_at = 0.0
        self.target_width = 620  # Tamanho padrão, será ajustado dinamicamente
        self.current_error_msg = ""
        self.is_online = False
        self.connectivity_status = "connecting"
        self.last_frame_at = None
        self._last_media_capability_check = 0.0
        self._incoming_audio_available = False
        self._live_audio_player = LiveAudioPlayer(
            FFMPEG_EXE,
            callback=self._on_live_audio_state_from_worker,
        )
        self._ptz_controller = None
        self._ptz_config_fingerprint = None
        
        # Botão de cabeçalho para expandir/recolher
        self.header_btn = tk.Button(
            self,
            text=f" ▶️ CÂMERA: {stream_name.upper()}",
            font=("Segoe UI", 9, "bold"),
            fg=TEXT_COLOR,
            bg="#161822",
            activebackground="#1F2232",
            activeforeground=TEXT_COLOR,
            bd=0,
            highlightbackground="#1F2232",
            highlightthickness=1,
            cursor="hand2",
            padx=12,
            pady=6,
            anchor="w",
            command=self.toggle
        )
        self.header_btn.pack(fill="x", pady=2)
        
        # Hover bindings para o cabeçalho
        self.header_btn.bind("<Enter>", lambda e: self.header_btn.configure(bg="#1F2232"))
        self.header_btn.bind("<Leave>", lambda e: self.header_btn.configure(bg="#111827" if self.expanded else "#161822"))
        
        # Frame de conteúdo que será exibido/ocultado
        self.body_frame = tk.Frame(self, bg="#020204")
        
        # Label para renderização da imagem (com cursor de clique e atalho de tela cheia)
        self.video_lbl = tk.Label(self.body_frame, bg="#020204", cursor="hand2")
        self.video_lbl.pack(pady=4)
        self.video_lbl.bind("<Double-Button-1>", lambda e: self.open_fullscreen())

        self.camera_media_status = tk.Frame(self.body_frame, bg="#0B1220", padx=10, pady=5)
        self.camera_media_status.pack(fill="x", padx=4, pady=(0, 2))
        self.lbl_media_status = tk.Label(
            self.camera_media_status,
            text="Gravação: verificando áudio",
            font=("Segoe UI", 8, "bold"),
            fg="#FBBF24",
            bg="#0B1220",
            anchor="w",
        )
        self.lbl_media_status.pack(side="left")
        self.lbl_ptz_status = tk.Label(
            self.camera_media_status,
            text="Movimento: verificando",
            font=("Segoe UI", 8),
            fg="#9CA3AF",
            bg="#0B1220",
            anchor="e",
        )
        self.lbl_ptz_status.pack(side="right")
        
        # Frame de controles inferiores da câmera (Barra de Ações Estilizada)
        self.controls_frame = tk.Frame(self.body_frame, bg="#111827", bd=1, relief="flat")
        self.controls_frame.pack(fill="x", padx=4, pady=(2, 4))
        
        # Botão Pasta de Gravações
        self.btn_folder = tk.Button(
            self.controls_frame,
            text=" 📂 Pasta Gravada",
            font=("Segoe UI", 8, "bold"),
            fg=TEXT_COLOR,
            bg="#111827",
            activebackground="#1F2937",
            activeforeground=TEXT_COLOR,
            bd=0,
            cursor="hand2",
            padx=10,
            pady=4,
            command=self.open_recordings_folder
        )
        self.btn_folder.pack(side="left", padx=4, pady=2)
        
        # Botão Link Web
        self.btn_link = tk.Button(
            self.controls_frame,
            text=" 🔗 Link Web",
            font=("Segoe UI", 8, "bold"),
            fg=TEXT_COLOR,
            bg="#111827",
            activebackground="#1F2937",
            activeforeground=TEXT_COLOR,
            bd=0,
            cursor="hand2",
            padx=10,
            pady=4,
            command=self.copy_camera_link
        )
        self.btn_link.pack(side="left", padx=4, pady=2)

        # Botão Recarregar Transmissão
        self.btn_reconnect = tk.Button(
            self.controls_frame,
            text=" 🔄 Recarregar",
            font=("Segoe UI", 8, "bold"),
            fg=TEXT_COLOR,
            bg="#111827",
            activebackground="#1F2937",
            activeforeground=TEXT_COLOR,
            bd=0,
            cursor="hand2",
            padx=10,
            pady=4,
            command=self.force_reconnect
        )
        self.btn_reconnect.pack(side="left", padx=4, pady=2)

        self.btn_live_audio = tk.Button(
            self.controls_frame,
            text=" 🔇 Sem microfone",
            font=("Segoe UI", 8, "bold"),
            fg=TEXT_COLOR,
            bg="#111827",
            activebackground="#065F46",
            activeforeground="#FFFFFF",
            disabledforeground="#6B7280",
            bd=0,
            cursor="arrow",
            padx=10,
            pady=4,
            state="disabled",
            command=self.toggle_live_audio,
        )
        self.btn_live_audio.pack(side="left", padx=4, pady=2)

        # Botão Tela Cheia
        self.btn_fullscreen = tk.Button(
            self.controls_frame,
            text=" 📺 Tela Cheia",
            font=("Segoe UI", 8, "bold"),
            fg=TEXT_COLOR,
            bg="#111827",
            activebackground="#1F2937",
            activeforeground=TEXT_COLOR,
            bd=0,
            cursor="hand2",
            padx=10,
            pady=4,
            command=self.open_fullscreen
        )
        self.btn_fullscreen.pack(side="right", padx=10, pady=2)

        self.ptz_frame = tk.Frame(self.controls_frame, bg="#111827", padx=4, pady=1)
        self.ptz_frame.pack(side="right", padx=2, pady=1)
        tk.Label(
            self.ptz_frame,
            text="PTZ",
            font=("Segoe UI", 8, "bold"),
            fg="#D1D5DB",
            bg="#111827",
        ).pack(side="left", padx=(2, 5))
        self.ptz_buttons = []
        for symbol, direction in (("←", "LEFT"), ("↑", "UP"), ("↓", "DOWN"), ("→", "RIGHT")):
            button = tk.Button(
                self.ptz_frame,
                text=symbol,
                font=("Segoe UI Symbol", 11, "bold"),
                fg=TEXT_COLOR,
                bg="#1F2937",
                activebackground="#2563EB",
                activeforeground="#FFFFFF",
                disabledforeground="#6B7280",
                bd=0,
                width=3,
                height=1,
                cursor="hand2",
                command=lambda selected=direction: self.move_camera(selected),
            )
            button.pack(side="left", padx=2)
            self.ptz_buttons.append(button)
        self.btn_ptz_stop = tk.Button(
            self.ptz_frame,
            text="■",
            font=("Segoe UI Symbol", 9, "bold"),
            fg="#FCA5A5",
            bg="#1F2937",
            activebackground="#991B1B",
            activeforeground="#FFFFFF",
            disabledforeground="#6B7280",
            bd=0,
            width=3,
            cursor="hand2",
            command=self.stop_camera_movement,
        )
        self.btn_ptz_stop.pack(side="left", padx=(6, 2))
        self.ptz_buttons.append(self.btn_ptz_stop)
        self.btn_ptz_config = tk.Button(
            self.ptz_frame,
            text="⚙",
            font=("Segoe UI Symbol", 10, "bold"),
            fg=TEXT_COLOR,
            bg="#1F2937",
            activebackground="#374151",
            activeforeground="#FFFFFF",
            bd=0,
            cursor="hand2",
            width=3,
            padx=2,
            pady=2,
            command=self.open_ptz_settings,
        )
        self.btn_ptz_config.pack(side="left", padx=(6, 2))

        # Hover styling effects
        self.app.setup_button_hover(self.btn_folder, "#111827", "#1F2937")
        self.app.setup_button_hover(self.btn_link, "#111827", "#1F2937")
        self.app.setup_button_hover(self.btn_reconnect, "#111827", "#1F2937")
        self.app.setup_button_hover(self.btn_live_audio, "#111827", "#065F46")
        self.app.setup_button_hover(self.btn_fullscreen, "#111827", "#1F2937")
        self.app.setup_button_hover(self.btn_ptz_config, "#1F2937", "#374151")
        self.refresh_ptz_configuration()

    def _stream_url(self):
        streams = (globals().get("CONFIG") or {}).get("streams") or {}
        return streams.get(self.stream_name, "") if isinstance(streams, dict) else ""

    def refresh_ptz_configuration(self):
        config = normalize_tuya_cloud_config((globals().get("CONFIG") or {}).get("tuya_cloud"))
        device_id = extract_tuya_device_id(self._stream_url())
        enabled = bool(config and device_id)
        state = "normal" if enabled else "disabled"
        cursor = "hand2" if enabled else "arrow"
        for button in getattr(self, "ptz_buttons", []):
            button.configure(state=state, cursor=cursor)
        if not device_id:
            text = "Movimento: fonte sem PTZ Tuya"
        elif not config:
            text = "Movimento: requer Tuya Cloud"
        else:
            text = "Movimento: pronto"
        self.lbl_ptz_status.configure(text=text, fg="#34D399" if enabled else "#FBBF24")

    def _get_ptz_controller(self):
        config = normalize_tuya_cloud_config((globals().get("CONFIG") or {}).get("tuya_cloud"))
        device_id = extract_tuya_device_id(self._stream_url())
        if not config or not device_id:
            raise ValueError("configure o acesso Tuya Cloud antes de mover a câmera")
        fingerprint = (
            config.get("access_id"),
            config.get("endpoint"),
            config.get("secret_protected"),
            device_id,
        )
        if self._ptz_controller is not None and self._ptz_config_fingerprint == fingerprint:
            return self._ptz_controller
        if self._ptz_controller is not None:
            self._ptz_controller.request_stop()
        access_id, secret, endpoint = load_tuya_cloud_credentials(config)
        client = TuyaCloudClient(access_id, secret, endpoint)
        self._ptz_controller = TuyaPtzPulseController(
            lambda direction: client.move(device_id, direction),
            duration=0.45,
            callback=self._on_ptz_state_from_worker,
        )
        self._ptz_config_fingerprint = fingerprint
        return self._ptz_controller

    def move_camera(self, direction):
        try:
            controller = self._get_ptz_controller()
            if not controller.pulse(direction):
                self.lbl_ptz_status.configure(text="Movimento: aguarde", fg="#FBBF24")
        except Exception as error:
            self.lbl_ptz_status.configure(text="Movimento: configuração inválida", fg="#F87171")
            self.app.add_log(
                f"[PTZ][{self.stream_name.upper()}] Controle indisponível: {error}",
                "tag_erro",
            )
            messagebox.showerror("Movimento da câmera", str(error))

    def stop_camera_movement(self):
        if self.request_ptz_stop():
            self.lbl_ptz_status.configure(text="Movimento: parando", fg="#FBBF24")

    def request_ptz_stop(self):
        if self._ptz_controller is None:
            return False
        self._ptz_controller.request_stop()
        return True

    def _on_ptz_state_from_worker(self, state, detail):
        def apply_state():
            if not self.winfo_exists():
                return
            if state == "moving":
                self.lbl_ptz_status.configure(text="Movimento: executando", fg="#60A5FA")
            elif state == "idle":
                self.lbl_ptz_status.configure(text="Movimento: pronto", fg="#34D399")
            else:
                self.lbl_ptz_status.configure(text="Movimento: falhou", fg="#F87171")
                self.app.add_log(
                    f"[PTZ][{self.stream_name.upper()}] Falha de controle: {detail}",
                    "tag_erro",
                )
        try:
            self.app.root.after(0, apply_state)
        except Exception:
            pass

    def close_ptz(self, timeout=6.0):
        if self._ptz_controller is None:
            return True
        return self._ptz_controller.close(timeout=timeout)

    def _on_live_audio_state_from_worker(self, state, detail):
        def apply_state():
            if not self.winfo_exists():
                return
            if state == "playing":
                self.btn_live_audio.configure(
                    text=" 🔊 Silenciar",
                    state="normal",
                    cursor="hand2",
                    bg="#065F46",
                )
            elif state in {"connecting", "reconnecting"}:
                self.btn_live_audio.configure(
                    text=" 🔇 Cancelar escuta",
                    state="normal",
                    cursor="hand2",
                    bg="#92400E",
                )
            elif self._incoming_audio_available:
                label = " 🔇 Tentar ouvir" if state in {"error", "unavailable"} else " 🔇 Ouvir"
                self.btn_live_audio.configure(
                    text=label,
                    state="normal",
                    cursor="hand2",
                    bg="#111827",
                )
            else:
                self.btn_live_audio.configure(
                    text=" 🔇 Sem microfone",
                    state="disabled",
                    cursor="arrow",
                    bg="#111827",
                )
        try:
            self.app.root.after(0, apply_state)
        except Exception:
            pass

    def toggle_live_audio(self):
        if self._live_audio_player.is_running():
            self.stop_live_audio()
            self.app.add_log(
                f"[{self.stream_name.upper()}] Escuta ao vivo silenciada; gravação não foi alterada."
            )
            return
        if not self._incoming_audio_available:
            return
        try:
            require_trusted_binary(FFMPEG_EXE, "ffmpeg.exe")
            self.app.stop_other_live_audio(self.stream_name)
            if self._live_audio_player.start(self.stream_name):
                self.app.add_log(
                    f"[{self.stream_name.upper()}] Escuta ao vivo ligada; gravação continua independente."
                )
        except Exception as error:
            self.app.add_log(
                f"[{self.stream_name.upper()}] Escuta ao vivo indisponível: {error}",
                "tag_erro",
            )
            messagebox.showerror("Áudio da câmera", str(error))

    def stop_live_audio(self):
        return self._live_audio_player.stop(timeout=0.5)

    def close_live_audio(self, timeout=3.0):
        return self._live_audio_player.close(timeout=timeout)

    def open_ptz_settings(self):
        current_window = getattr(self, "_ptz_settings_window", None)
        if current_window is not None and current_window.winfo_exists():
            current_window.lift()
            current_window.focus_force()
            return

        current = normalize_tuya_cloud_config((globals().get("CONFIG") or {}).get("tuya_cloud"))
        stream_endpoint = infer_tuya_endpoint(self._stream_url())
        endpoint_labels = {
            "Américas": TUYA_ENDPOINTS["america"],
            "Europa": TUYA_ENDPOINTS["europe"],
            "China": TUYA_ENDPOINTS["china"],
            "Índia": TUYA_ENDPOINTS["india"],
        }
        selected_label = next(
            (label for label, endpoint in endpoint_labels.items()
             if endpoint == current.get("endpoint", stream_endpoint)),
            "Américas",
        )

        window = tk.Toplevel(self)
        self._ptz_settings_window = window
        window.title("Configurar movimento Tuya")
        window.configure(bg="#111827")
        window.resizable(False, False)
        window.transient(self.app.root)
        window.grab_set()

        content = tk.Frame(window, bg="#111827", padx=20, pady=18)
        content.pack(fill="both", expand=True)
        tk.Label(
            content,
            text="Controle de movimento da câmera",
            font=("Segoe UI", 12, "bold"),
            fg="#F9FAFB",
            bg="#111827",
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))

        access_var = tk.StringVar(value=current.get("access_id", ""))
        secret_var = tk.StringVar()
        center_var = tk.StringVar(value=selected_label)
        fields = (("Access ID", access_var, False), ("Access Secret", secret_var, True))
        for row, (label, variable, secret_field) in enumerate(fields, start=1):
            tk.Label(
                content,
                text=label,
                font=("Segoe UI", 9),
                fg="#D1D5DB",
                bg="#111827",
                anchor="w",
            ).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=6)
            tk.Entry(
                content,
                textvariable=variable,
                show="•" if secret_field else "",
                font=("Segoe UI", 9),
                bg="#1F2937",
                fg="#F9FAFB",
                insertbackground="#F9FAFB",
                relief="flat",
                width=42,
            ).grid(row=row, column=1, sticky="ew", pady=6, ipady=5)

        tk.Label(
            content,
            text="Data center",
            font=("Segoe UI", 9),
            fg="#D1D5DB",
            bg="#111827",
        ).grid(row=3, column=0, sticky="w", padx=(0, 12), pady=6)
        center_combo = ttk.Combobox(
            content,
            textvariable=center_var,
            values=list(endpoint_labels.keys()),
            state="readonly",
            width=39,
        )
        center_combo.grid(row=3, column=1, sticky="ew", pady=6)
        tk.Label(
            content,
            text=(
                "O segredo fica protegido pelo Windows. Deixe-o vazio para manter "
                "o valor já salvo."
            ),
            font=("Segoe UI", 8),
            fg="#9CA3AF",
            bg="#111827",
            justify="left",
            wraplength=430,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 14))

        actions = tk.Frame(content, bg="#111827")
        actions.grid(row=5, column=0, columnspan=2, sticky="e")

        def close_window():
            self._ptz_settings_window = None
            window.grab_release()
            window.destroy()

        def save_settings():
            global CONFIG
            previous = CONFIG.get("tuya_cloud", {})
            try:
                new_config = build_tuya_cloud_config(
                    access_var.get(),
                    endpoint_labels[center_var.get()],
                    secret_var.get(),
                    existing=current,
                )
                CONFIG["tuya_cloud"] = new_config
                if not salvar_config(CONFIG):
                    CONFIG["tuya_cloud"] = previous
                    raise RuntimeError("não foi possível salvar a configuração local")
                for widget in self.app.camera_widgets.values():
                    widget.refresh_ptz_configuration()
                self.app.add_log("Configuração Tuya Cloud salva com segredo protegido pelo Windows.")
                close_window()
            except Exception as error:
                messagebox.showerror("Configurar movimento", str(error), parent=window)

        tk.Button(
            actions,
            text="Cancelar",
            font=("Segoe UI", 9),
            fg="#D1D5DB",
            bg="#374151",
            activebackground="#4B5563",
            activeforeground="#FFFFFF",
            bd=0,
            padx=14,
            pady=6,
            command=close_window,
        ).pack(side="left", padx=4)
        tk.Button(
            actions,
            text="Salvar",
            font=("Segoe UI", 9, "bold"),
            fg="#FFFFFF",
            bg="#2563EB",
            activebackground="#1D4ED8",
            activeforeground="#FFFFFF",
            bd=0,
            padx=18,
            pady=6,
            command=save_settings,
        ).pack(side="left", padx=4)
        window.protocol("WM_DELETE_WINDOW", close_window)
        window.update_idletasks()
        x = self.app.root.winfo_rootx() + max(20, (self.app.root.winfo_width() - window.winfo_width()) // 2)
        y = self.app.root.winfo_rooty() + max(20, (self.app.root.winfo_height() - window.winfo_height()) // 2)
        window.geometry(f"+{x}+{y}")

    def refresh_media_capabilities(self, force=False):
        now = time.time()
        if not force and now - self._last_media_capability_check < 10.0:
            return
        self._last_media_capability_check = now
        streams_data = self.app.get_cached_streams_data()
        capabilities = get_go2rtc_stream_capabilities(streams_data or {}, self.stream_name)
        if capabilities["incoming_audio"]:
            text, color = "Gravação bruta: vídeo + áudio", "#34D399"
        elif capabilities["talkback_audio"]:
            text, color = "Gravação bruta: SEM ÁUDIO | câmera oferece somente interfone", "#FBBF24"
        elif capabilities["media_active"]:
            text, color = "Gravação bruta: SEM ÁUDIO | microfone não fornecido", "#FBBF24"
        else:
            text, color = "Gravação bruta: aguardando mídia", "#9CA3AF"

        def apply_status():
            if self.winfo_exists():
                self._incoming_audio_available = bool(capabilities["incoming_audio"])
                self.lbl_media_status.configure(text=text, fg=color)
                if self._incoming_audio_available:
                    if not self._live_audio_player.is_running():
                        self.btn_live_audio.configure(
                            text=" 🔇 Ouvir",
                            state="normal",
                            cursor="hand2",
                            bg="#111827",
                        )
                else:
                    self.btn_live_audio.configure(
                        text=" 🔇 Sem microfone",
                        state="disabled",
                        cursor="arrow",
                        bg="#111827",
                    )
                    if self._live_audio_player.is_running():
                        threading.Thread(
                            target=self.stop_live_audio,
                            daemon=True,
                            name=f"stop-live-audio-{self.stream_name}",
                        ).start()
        try:
            self.app.root.after(0, apply_status)
        except Exception:
            pass

    def toggle(self):
        if self.expanded:
            self.collapse()
        else:
            self.expand()

    def update_header_text(self):
        if self.connectivity_status == "online":
            status_badge = "  [🟢 ONLINE]"
        elif self.connectivity_status == "offline":
            status_badge = "  [🔴 OFFLINE]"
        elif self.connectivity_status == "standby":
            status_badge = "  [🟠 EM ESPERA]"
        else:
            status_badge = "  [🟠 RECONECTANDO]"
        if self.expanded:
            self.header_btn.configure(text=f" ▼️ RECOLHER: {self.stream_name.upper()}{status_badge}", bg="#111827")
        else:
            self.header_btn.configure(text=f" ▶️ CÂMERA: {self.stream_name.upper()}{status_badge}", bg="#161822")

    def set_connectivity_status(self, status):
        if status not in {"online", "connecting", "reconnecting", "offline", "standby"}:
            status = "reconnecting"
        self.connectivity_status = status
        self.is_online = status == "online"
        self.update_header_text()

    def expand(self):
        self.expanded = True
        self.update_header_text()
        self.pack_configure(fill="both", expand=True)
        self.body_frame.pack(fill="both", expand=True)
        self.start_stream()
        self._recalc_camera_sizes()

    def collapse(self):
        self.expanded = False
        self.update_header_text()
        if not self.continuous_analysis:
            self.stop_stream()
        self.stop_live_audio()
        self.body_frame.pack_forget()
        self.pack_configure(fill="x", expand=False)
        self._recalc_camera_sizes()

    def set_continuous_analysis(self, enabled):
        self.continuous_analysis = bool(enabled)
        if self.continuous_analysis:
            self.start_stream()
        elif not self.expanded:
            self.stop_stream()

    def _recalc_camera_sizes(self):
        """Recalcula o tamanho das câmeras com base em quantas estão expandidas e na altura do container"""
        if not hasattr(self.app, 'camera_widgets'):
            return
        expanded_count = sum(1 for w in self.app.camera_widgets.values() if w.expanded)

        try:
            self.master.pack_configure(
                fill="both" if expanded_count else "x",
                expand=bool(expanded_count),
            )
            for widget in self.app.camera_widgets.values():
                widget.pack_configure(
                    fill="both" if widget.expanded else "x",
                    expand=widget.expanded,
                )
        except Exception:
            pass
        
        # Obtém as dimensões disponíveis da coluna direita
        try:
            container_width = self.master.winfo_width()
            container_height = self.master.winfo_height()
            if container_width < 100: container_width = 650
            if container_height < 100: container_height = 800
        except Exception:
            container_width = 650
            container_height = 800
        
        target_w = container_width - 30  # Margem lateral
        
        # Se ambas estão abertas, limita a largura para caber verticalmente na tela sem transbordar
        if expanded_count >= 2:
            max_h_per_cam = (container_height - 120) // 2
            target_w = min(target_w, int(max_h_per_cam * 16 / 9))
            target_w = max(target_w, 320)  # Garante largura mínima funcional
            
        for w in self.app.camera_widgets.values():
            w.target_width = target_w

    def is_corrupt_frame(self, pil_image):
        return not assess_frame_integrity(pil_image)["valid"]

    def inspect_preview_frame(self, pil_image):
        result = assess_frame_integrity(pil_image)
        should_reconnect = self._frame_integrity_guard.observe(
            result["valid"], result.get("reason")
        )
        if result["valid"]:
            return True, False

        now = time.time()
        streak = self._frame_integrity_guard.consecutive_rejected
        if streak == 1 or should_reconnect or now - self._last_integrity_log_at >= 30.0:
            action = (
                "reconectando somente o preview"
                if should_reconnect
                else "aguardando o proximo quadro valido"
            )
            self.app.add_log(
                f"[{self.stream_name.upper()}] Quadro visual invalido descartado "
                f"({result.get('reason', 'unknown')}); {action}.",
                "tag_atencao",
            )
            self._last_integrity_log_at = now
        return False, should_reconnect

    def start_stream(self):
        if self.running and self.thread is not None and self.thread.is_alive():
            return False
        self.running = True
        self.thread = threading.Thread(target=self.stream_loop, daemon=True)
        self.thread.start()
        threading.Thread(
            target=self.refresh_media_capabilities,
            kwargs={"force": True},
            daemon=True,
        ).start()
        return True

    def stop_stream(self):
        self.running = False
        with self._display_lock:
            self._pending_display_image = None
        self._frame_integrity_guard.reset()
        # Fecha a conexão MJPEG ativa para liberar imediatamente
        if hasattr(self, '_mjpeg_response') and self._mjpeg_response:
            try:
                self._mjpeg_response.close()
            except Exception:
                pass
            self._mjpeg_response = None
        self.video_lbl.configure(image="")
        self.photo = None

    def _read_mjpeg_frames(self, url):
        """Conecta ao stream MJPEG e gera frames JPEG continuamente via generator"""
        req = urllib.request.Request(url)
        response = urllib.request.urlopen(req, timeout=8.0)
        self._mjpeg_response = response
        
        buf = b''
        try:
            while self.running:
                chunk = response.read(16384)  # Lê blocos grandes para performance
                if not chunk:
                    break
                buf += chunk
                
                # Busca frames JPEG completos pelos marcadores (SOI: FFD8, EOI: FFD9)
                while True:
                    start = buf.find(b'\xff\xd8')
                    if start == -1:
                        buf = buf[-2:]  # Mantém últimos 2 bytes para caso de marcador parcial
                        break
                    end = buf.find(b'\xff\xd9', start + 2)
                    if end == -1:
                        buf = buf[start:]  # Mantém do início do JPEG em diante
                        if len(buf) > 2 * 1024 * 1024:  # Proteção: buffer > 2MB = frame corrompido
                            buf = b''
                        break
                    
                    # Frame JPEG completo extraído
                    jpeg_data = buf[start:end + 2]
                    buf = buf[end + 2:]
                    yield jpeg_data
        finally:
            try:
                response.close()
            except Exception:
                pass
            self._mjpeg_response = None

    def stream_loop(self):
        """Mantem exibicao fluida ou amostragem limitada quando recolhida."""
        mjpeg_url = f"http://127.0.0.1:1984/api/stream.mjpeg?src={self.stream_name}_mjpeg"
        time.sleep(0.3)
        
        last_frame_received_time = 0
        
        while self.running:
            self.refresh_media_capabilities()
            bridge_available = (
                self.app.managed_go2rtc_running()
                or getattr(self.app, "_last_go2rtc_ok", False)
            )
            if not bridge_available:
                self.show_error_message("Ponte RTSP offline")
                time.sleep(1.0)
                continue
            
            try:
                last_frame_time = 0
                for jpeg_data in self._read_mjpeg_frames(mjpeg_url):
                    if not self.running:
                        break
                    
                    # Rate limiter para não sobrecarregar a CPU
                    now = time.time()
                    min_interval = 0.066 if self.expanded else 0.5
                    if (now - last_frame_time) < min_interval:
                        continue
                    last_frame_time = now
                    
                    try:
                        source_image = Image.open(io.BytesIO(jpeg_data))
                        source_image.load()
                        frame_valid, should_reconnect = self.inspect_preview_frame(source_image)
                        if not frame_valid:
                            if should_reconnect:
                                try:
                                    self._mjpeg_response.close()
                                except Exception:
                                    pass
                                self._frame_integrity_guard.reset()
                                break
                            continue
                        if self.running:
                            self.app.submit_vision_frame(self.stream_name, source_image)
                            last_frame_received_time = time.time()
                            if self.expanded:
                                # Redimensiona apenas quando o quadro sera exibido.
                                tw = self.target_width
                                th = int(tw * 9 / 16)
                                image = source_image.resize(
                                    (tw, th), Image.Resampling.BILINEAR
                                )
                                image = self.apply_identity_overlay(image)
                                self.update_image(image)
                            else:
                                self.current_error_msg = ""
                                self.is_online = True
                                self.connectivity_status = "online"
                                self.last_frame_at = last_frame_received_time
                    except Exception:
                        pass  # Frame corrompido, pula para o próximo
                
                # Se a conexão com o gerador encerrar normalmente, espera 1 segundo antes de tentar novamente
                if self.running:
                    time.sleep(1.0)
                        
            except Exception as e:
                if self.running:
                    # Mostra tela de reconexão apenas se ficar mais de 10 segundos sem vídeo
                    if time.time() - last_frame_received_time > 10:
                        self.show_error_message("Reconectando...")
                    time.sleep(1.0)

    def show_error_message(self, msg):
        if not self.running:
            return
        if getattr(self, "current_error_msg", "") == msg:
            return
        self.current_error_msg = msg
        self.is_online = False
        self.connectivity_status = "reconnecting"
        self.app.root.after(0, lambda: self.video_lbl.configure(image="", text=msg, fg=ORANGE_COLOR, font=("Segoe UI", 9, "bold"), compound="center"))
        self.app.root.after(0, self.update_header_text)

    def apply_identity_overlay(self, pil_image):
        try:
            overlay = self.app.get_vision_identity_overlay(self.stream_name)
            return render_identity_overlay(pil_image, overlay)
        except Exception:
            return pil_image

    def update_image(self, pil_image):
        self.current_error_msg = ""
        self.is_online = True
        self.connectivity_status = "online"
        self.last_frame_at = time.time()
        with self._display_lock:
            self._pending_display_image = pil_image
            if self._display_update_scheduled:
                return
            self._display_update_scheduled = True
        try:
            self.app.root.after(0, self._apply_pending_image)
        except Exception:
            with self._display_lock:
                self._pending_display_image = None
                self._display_update_scheduled = False

    def _apply_pending_image(self):
        with self._display_lock:
            pil_image = self._pending_display_image
            self._pending_display_image = None
            self._display_update_scheduled = False
        if not self.running or pil_image is None:
            return
        try:
            old_photo = getattr(self, "photo", None)
            photo = ImageTk.PhotoImage(pil_image)
            self.photo = photo
            self.video_lbl.configure(image=photo, text="", compound="none")
            self.video_lbl.image = photo
            self.update_header_text()
            if old_photo:
                try:
                    self.app.root.call("image", "delete", old_photo)
                except Exception:
                    pass
        except Exception:
            pass

    def open_fullscreen(self):
        fs_win = tk.Toplevel(self.app.root)
        fs_win.title(f"Visualizador Câmera: {self.stream_name.upper()}")
        fs_win.configure(bg="#000000")
        fs_win.state("zoomed")
        
        fs_lbl = tk.Label(fs_win, bg="#000000")
        fs_lbl.pack(fill="both", expand=True)
        
        fs_running = [True]
        fs_response = [None]
        
        def fs_loop():
            mjpeg_url = f"http://127.0.0.1:1984/api/stream.mjpeg?src={self.stream_name}_mjpeg"
            integrity_guard = FrameIntegrityGuard()
            while fs_running[0]:
                try:
                    req = urllib.request.Request(mjpeg_url)
                    response = urllib.request.urlopen(req, timeout=8.0)
                    fs_response[0] = response
                    
                    buf = b''
                    last_frame_time = 0
                    min_interval = 0.05  # ~20 FPS para tela cheia (mais fluido)
                    
                    while fs_running[0]:
                        chunk = response.read(32768)
                        if not chunk:
                            break
                        buf += chunk
                        
                        while True:
                            start = buf.find(b'\xff\xd8')
                            if start == -1:
                                buf = buf[-2:]
                                break
                            end = buf.find(b'\xff\xd9', start + 2)
                            if end == -1:
                                buf = buf[start:]
                                if len(buf) > 2 * 1024 * 1024:
                                    buf = b''
                                break
                            
                            jpeg_data = buf[start:end + 2]
                            buf = buf[end + 2:]
                            
                            now = time.time()
                            if (now - last_frame_time) < min_interval:
                                continue
                            last_frame_time = now
                            
                            try:
                                w = fs_lbl.winfo_width()
                                h = fs_lbl.winfo_height()
                                if w < 100 or h < 100:
                                    w = fs_win.winfo_screenwidth()
                                    h = fs_win.winfo_screenheight()
                                
                                image = Image.open(io.BytesIO(jpeg_data))
                                image.load()
                                integrity = assess_frame_integrity(image)
                                if not integrity["valid"]:
                                    if integrity_guard.observe(False, integrity.get("reason")):
                                        integrity_guard.reset()
                                        response.close()
                                        break
                                    continue
                                integrity_guard.observe(True)
                                # Escala mantendo proporção original (sem corte)
                                img_aspect = image.width / image.height
                                win_aspect = w / h
                                if win_aspect > img_aspect:
                                    new_h = h
                                    new_w = int(h * img_aspect)
                                else:
                                    new_w = w
                                    new_h = int(w / img_aspect)
                                
                                image = image.resize((new_w, new_h), Image.Resampling.BILINEAR)
                                
                                image = self.apply_identity_overlay(image)

                                photo = ImageTk.PhotoImage(image)
                                fs_lbl.photo = photo
                                if fs_running[0]:
                                    fs_win.after(0, lambda p=photo: fs_lbl.configure(image=p))
                            except Exception:
                                pass
                    
                    response.close()
                except Exception:
                    if fs_running[0]:
                        time.sleep(1.0)
                finally:
                    fs_response[0] = None
                    
        fs_thread = threading.Thread(target=fs_loop, daemon=True)
        fs_thread.start()
        
        def on_close():
            fs_running[0] = False
            if fs_response[0]:
                try:
                    fs_response[0].close()
                except Exception:
                    pass
            fs_win.destroy()
            
        fs_win.protocol("WM_DELETE_WINDOW", on_close)

    def open_recordings_folder(self):
        try:
            if self.stream_name not in self.app.streams:
                raise Exception("camera nao encontrada na configuracao")
            stream_index = self.app.streams.index(self.stream_name)
            folder_path = self.app.get_gdrive_dir(self.stream_name, stream_index)
            if not folder_path:
                raise Exception("destino do HD ainda nao foi configurado")
            os.makedirs(folder_path, exist_ok=True)
            os.startfile(folder_path)
            self.app.add_log(f"Abrindo pasta de gravações da {self.stream_name}: {folder_path}")
        except Exception as e:
            self.app.add_log(f"Erro ao abrir pasta: {str(e)}", "tag_erro")

    def copy_camera_link(self):
        try:
            url = f"http://{self.app.local_ip}:1984/api/stream.mjpeg?src={self.stream_name}_mjpeg"
            self.app.root.clipboard_clear()
            self.app.root.clipboard_append(url)
            self.app.add_log(f"Link de visualização da {self.stream_name} copiado para a área de transferência!")
            messagebox.showinfo("Copiado", f"Link copiado:\n{url}")
        except Exception as e:
            self.app.add_log(f"Erro ao copiar link: {str(e)}", "tag_erro")

    def force_reconnect(self):
        self.app.add_log(f"Forçando reinicialização da transmissão de visualização: {self.stream_name}...")
        self.stop_stream()
        self.start_stream()

class CameraManagerApp:
    def __init__(self, root, silent=False, smoke_test_seconds=0):
        self.root = root
        self.silent = silent
        self.smoke_test_seconds = smoke_test_seconds
        
        # Registra gancho de encerramento seguro via atexit
        import atexit
        atexit.register(self.graceful_shutdown)
        
        # Variáveis de Controle de Threads
        self.running_monitor = True
        self.running_sync = True
        
        # Variáveis de Gravação em Memória (NVR Integrado)
        self.recording_active = {}
        self.recording_threads = {}
        self.recording_destinations = {}
        self.active_connections = {}
        self.reconnect_failures = {}
        self.status_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._ui_log_queue = queue.Queue(maxsize=1000)
        self._ui_control_queue = queue.Queue(maxsize=8)
        self._external_log_queue = queue.Queue(maxsize=500)
        self._lifecycle_lock = threading.RLock()
        self._startup_ready = threading.Event()
        self._stop_lock = threading.Lock()
        self._shutdown_request_lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._retention_lock = threading.Lock()
        self._scan_state_path = os.path.join(LOGS_DIR, "integrity_scan_state.json")
        self._health_lock = threading.Lock()
        self._health_snapshot = None
        self._health_issue_keys = set()
        self._intelligence_key = None
        self._last_health_check = 0.0
        self._last_go2rtc_ok = False
        self._analytics_store = None
        self._biometric_store = None
        self._analytics_collector = None
        self._vision_coordinator = None
        self._face_service = None
        self._evidence_archive = None
        self._analytics_window = None
        self._analytics_start_lock = threading.Lock()
        self._analytics_starting = False
        self._analytics_open_when_ready = False
        self._analytics_shutdown = False
        self._analytics_runtime_error = None
        self._vision_guard_checked_at = 0.0
        self._vision_guard_reason = None
        self._usb_report_cache = None
        self._usb_report_cache_time = 0.0
        self._kernel_144_state_path = os.path.join(LOGS_DIR, "kernel_144_baseline.json")
        self._kernel_144_session_baseline = None
        self._resource_samples = []
        self._smart_snapshot = {"status": "pending", "drives": [], "error": None}
        self._power_snapshot = {"status": "unknown", "battery_percent": None}
        self.go2rtc_restart_count = 0
        self._go2rtc_process = None
        self._recorder_owner_token = secrets.token_hex(16)
        self.recording_started_at = {}
        self.stream_bytes_written = {}
        self.stream_last_data_at = {}
        self.camera_connectivity_states = {}
        self.camera_signal_samples = {}
        self.alerted_duplicates = {} # Evita exibir alerta popup repetidamente
        
        # Cache de performance para evitar chamadas repetidas
        self._cached_streams_data = None
        self._cached_streams_time = 0
        self._cached_backup_stats = (0, 0)
        self._cached_backup_time = 0
        self._last_recording_cache = {}
        
        self.streams = [s for s in self.parse_streams() if not s.endswith("_live") and not s.endswith("_mjpeg")]
        self.local_ip = self.get_local_ip()
        
        # Controle de energia (prevenção de suspensão e monitoramento de bateria/no-break)
        self.on_battery = False
        self.prevent_sleep_var = tk.BooleanVar(value=True)
        self.apply_prevent_sleep(True)
        
        # 1. Configura título e layout (aumentado para maior visibilidade)
        self.root.title(f"Painel Câmeras Farmácia — NVR v{VERSION}")
        self.root.geometry("1400x920")
        self.root.configure(bg=BG_COLOR)
        self.root.minsize(1280, 850)
        
        # Vincular redimensionamento dinâmico do painel
        self.root.bind("<Configure>", self.on_window_resize)
        
        self.setup_styles()
        self.create_widgets()
        self.root.after(200, self.drain_ui_log_queue)
        self.root.after(100, self.drain_ui_control_queue)
        self._external_log_thread = threading.Thread(target=self.external_log_writer_loop, daemon=True)
        self._external_log_thread.start()
        self.root.after(300, self.start_wimi_analytics)
        
        # Inicializa a máquina de estados do botão e animação
        self.button_state = "STOPPED"
        self.animate_pulse()
        
        # Agenda escaneamento automático a cada 3 horas (3 * 3600 * 1000 ms)
        self.root.after(10800000, self.trigger_periodic_scan)
        self.root.after(14400000, self.trigger_periodic_retention)
        
        # Agenda diagnóstico automático a cada 6 horas (6 * 3600 * 1000 ms)
        self.root.after(21600000, self.trigger_periodic_diagnostics)
        
        # Intercepta o fechamento no X do Tkinter para desligar de forma segura
        self.root.protocol("WM_DELETE_WINDOW", self.on_close_window)
        
        # Thread de verificação de atualizações no GitHub
        threading.Thread(target=self.check_for_updates_thread, daemon=True).start()
        self.root.after(3600000, self.trigger_periodic_update)
            
        # Sinais do sistema para encerramento limpo (SIGINT, SIGTERM)
        import signal
        try:
            signal.signal(signal.SIGINT, self.handle_exit_signal)
            signal.signal(signal.SIGTERM, self.handle_exit_signal)
        except Exception:
            pass

        # Auto-provisionamento de rede e recuperação de órfãos
        self.auto_provision_system()
        self.limpar_arquivos_temporarios_orfaos()
        self.verificar_saude_discos_smart()
        self.limpar_processos_ffmpeg_zumbis(sync=True)
        if not self.silent:
            self._startup_ready.set()

        # 2. Inicia a thread de monitoramento em tempo real
        self.running_monitor = True
        self.monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.monitor_thread.start()
        
        # 3. Inicia a thread de sincronização de backups locais em segundo plano
        self.running_sync = True
        self.sync_thread = threading.Thread(target=self.background_sync_loop, daemon=True)
        self.sync_thread.start()
        
        # 4. No modo silencioso, inicia as gravações automaticamente
        if self.silent:
            threading.Thread(target=self.run_start_sequence, daemon=True).start()
        else:
            self.add_log(f"Painel NVR v{VERSION} iniciado. Câmeras: {', '.join(self.streams)}")

        # 5. Inicia o servidor de escuta de instância única
        self.iniciar_servidor_instancia()

        if self.smoke_test_seconds:
            self.add_log(
                f"[SMOKE] Ensaio real controlado iniciado por {self.smoke_test_seconds} segundos."
            )
            self.root.after(
                self.smoke_test_seconds * 1000,
                lambda: self.request_safe_shutdown("fim do ensaio real controlado"),
            )

    def iniciar_servidor_instancia(self):
        def server_loop():
            global _instance_socket
            if _instance_socket is None:
                return
            while True:
                try:
                    conn, _addr = _instance_socket.accept()
                except Exception:
                    break
                try:
                    with conn:
                        conn.settimeout(2.0)
                        data = conn.recv(1024).decode("utf-8").strip()
                        if data == "SHOW":
                            self.root.after(0, self.restaurar_janela_oculta)
                            conn.sendall(b"OK")
                        elif data == "STOP_SAFE":
                            conn.sendall(b"OK")
                            self.request_safe_shutdown("comando local --safe-stop")
                except Exception:
                    continue
        threading.Thread(target=server_loop, daemon=True).start()

    def request_safe_shutdown(self, reason):
        with self._shutdown_request_lock:
            if getattr(self, "_shutdown_requested", False):
                return
            self._shutdown_requested = True
        self.add_log(f"Encerramento seguro solicitado: {reason}.")
        threading.Thread(target=self.graceful_shutdown, daemon=False).start()

    def restaurar_janela_oculta(self):
        self.silent = False
        self.root.deiconify()
        self.root.state("zoomed")
        self.root.lift()
        self.root.focus_force()
        
        # Descarrega logs gerados durante o modo silencioso
        if hasattr(self, "_startup_logs"):
            logs_to_flush = self._startup_logs
            self._startup_logs = []
            for msg_item in logs_to_flush:
                self._append_to_log_widget(msg_item, self.infer_log_tag(msg_item))
                
        self.add_log("Janela restaurada a pedido do usuário.")

    def limpar_arquivos_temporarios_orfaos(self):
        """Remove apenas temporarios vazios; videos com dados ficam para recuperacao."""
        temp_dir = os.path.join(PROJ_DIR, "sistema", "gravando_temp")
        if not os.path.exists(temp_dir):
            return
            
        try:
            count = 0
            size = 0
            preserved = 0
            for root_dir, _, files in os.walk(temp_dir):
                for f in files:
                    if f.endswith((".ts", ".tmp")):
                        file_path = os.path.join(root_dir, f)
                        try:
                            file_size = os.path.getsize(file_path)
                            if file_size == 0:
                                size += file_size
                                os.remove(file_path)
                                count += 1
                            else:
                                preserved += 1
                        except Exception:
                            pass
            if count > 0 and not self.silent:
                self.add_log(f"🧹 [STARTUP] Limpeza concluída: removidos {count} arquivo(s) temporário(s) órfão(s) ({size / (1024*1024):.2f} MB liberados).")
            if preserved > 0 and not self.silent:
                self.add_log(f"[STARTUP] {preserved} video(s) temporario(s) com dados preservados para nova recuperacao.")
        except Exception:
            pass

    def verificar_saude_discos_smart(self):
        """Consulta o status informado pelo Windows sem bloquear indefinidamente."""
        def check():
            self._smart_snapshot = self.query_smart_status()
            if self._smart_snapshot["status"] == "degraded":
                for drive in self._smart_snapshot["drives"]:
                    if str(drive["status"]).upper() != "OK":
                        self.add_log(
                            f"[HEALTH][CRITICAL][SMART_DEGRADED] Status basico do Windows para '{drive['model']}': '{drive['status']}'.",
                            "tag_erro",
                        )
        threading.Thread(target=check, daemon=True).start()

    def query_smart_status(self):
        try:
            cmd = ["powershell", "-Command", "Get-WmiObject -Class Win32_DiskDrive | Select-Object Model, Status | ConvertTo-Json"]
            output = subprocess.check_output(
                cmd,
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if not output.strip():
                raise Exception("consulta retornou vazia")
            drives = json.loads(output)
            if isinstance(drives, dict):
                drives = [drives]
            normalized_drives = [
                {
                    "model": drive.get("Model", "Desconhecido"),
                    "status": drive.get("Status", "Desconhecido"),
                }
                for drive in drives
            ]
            unhealthy = [drive for drive in normalized_drives if str(drive["status"]).upper() != "OK"]
            return {
                "status": "degraded" if unhealthy else "ok",
                "drives": normalized_drives,
                "error": None,
                "telemetry_level": "basic_windows_status",
                "source": "Win32_DiskDrive.Status",
                "checked_at": datetime.now().isoformat(timespec="seconds"),
            }
        except Exception as error:
            return {
                "status": "unknown",
                "drives": [],
                "error": str(error),
                "telemetry_level": "basic_windows_status",
                "source": "Win32_DiskDrive.Status",
                "checked_at": datetime.now().isoformat(timespec="seconds"),
            }

    def limpar_processos_ffmpeg_zumbis(self, sync=False):
        """Busca e finaliza processos ffmpeg.exe órfãos rodando sob a pasta do projeto no Windows"""
        def clean():
            try:
                cmd = ["powershell", "-Command", "Get-Process ffmpeg -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*sistema\\\\go2rtc*' } | Stop-Process -Force"]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            except Exception:
                pass
        if sync:
            clean()
        else:
            threading.Thread(target=clean, daemon=True).start()

    def setup_button_hover(self, button, normal_bg, hover_bg):
        button.bind("<Enter>", lambda e: button.configure(bg=hover_bg))
        button.bind("<Leave>", lambda e: button.configure(bg=normal_bg))

    def stop_other_live_audio(self, selected_stream):
        for stream_name, widget in getattr(self, "camera_widgets", {}).items():
            if stream_name != selected_stream:
                widget.stop_live_audio()

    def setup_card_hover_glow(self, card, glow_color):
        card.bind("<Enter>", lambda e: card.configure(highlightbackground=glow_color))
        card.bind("<Leave>", lambda e: card.configure(highlightbackground="#1F2232"))

    def configure_badge_label(self, label, text, fg_color):
        bg_color = BADGE_BG_MAP.get(fg_color, "#1F2937")
        label.configure(
            text=f"  {text.strip()}  ",
            fg=fg_color,
            bg=bg_color,
            font=("Segoe UI", 8, "bold"),
            relief="flat",
            bd=0
        )

    def on_window_resize(self, event):
        if event.widget != self.root:
            return
        now = time.time()
        if not hasattr(self, "_last_resize_time"):
            self._last_resize_time = 0
        if now - self._last_resize_time < 0.15:
            return
        self._last_resize_time = now
        if hasattr(self, "camera_widgets"):
            for w in self.camera_widgets.values():
                w._recalc_camera_sizes()

    def parse_streams(self):
        configured_streams = normalize_streams_config((globals().get("CONFIG") or {}).get("streams"))
        if configured_streams:
            return list(configured_streams.keys())

        yaml_path = os.path.join(PROJ_DIR, "sistema", "go2rtc", "go2rtc.yaml")
        streams = []
        if not os.path.exists(yaml_path):
            return ["farmacia", "farmacia2"]
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                content = f.read()
            lines = content.splitlines()
            in_streams = False
            for line in lines:
                line_strip = line.strip()
                if line_strip.startswith("streams:"):
                    in_streams = True
                    continue
                if in_streams:
                    if line.startswith(" ") or line.startswith("\t"):
                        if ":" in line_strip:
                            name = line_strip.split(":")[0].strip()
                            if name.startswith('"'):
                                try:
                                    name = json.loads(name)
                                except Exception:
                                    name = name.strip('"')
                            if name and not name.startswith("#"):
                                streams.append(name)
                    else:
                        if line_strip != "" and not line_strip.startswith("#"):
                            in_streams = False
        except Exception:
            pass
        if not streams:
            return ["farmacia", "farmacia2"]
        return streams

    def get_gdrive_dir(self, stream_name, index):
        if not GDRIVE_ROOT:
            return ""
        folder_map = normalize_storage_folder_map(
            CONFIG.get("storage_folder_map"),
            list(normalize_streams_config(CONFIG.get("streams")).keys()) or self.streams,
        )
        folder_name = folder_map.get(stream_name, f"camera {index + 1}")
        return os.path.join(GDRIVE_ROOT, folder_name)

    def get_camera_storage_dirs(self):
        dirs = []
        for idx, stream in enumerate(self.streams):
            path = self.get_gdrive_dir(stream, idx)
            if path and path not in dirs:
                dirs.append(path)
        return dirs

    def get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=BG_COLOR, foreground=TEXT_COLOR)
        style.configure("TLabel", background=BG_COLOR, foreground=TEXT_COLOR, font=("Segoe UI", 10))
        style.configure("Main.TNotebook", background=BG_COLOR, borderwidth=0, tabmargins=(14, 0, 0, 0))
        style.configure(
            "Main.TNotebook.Tab",
            background="#111827",
            foreground="#9CA3AF",
            padding=(20, 9),
            borderwidth=0,
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Main.TNotebook.Tab",
            background=[("selected", "#1F2937"), ("active", "#172033")],
            foreground=[("selected", TEXT_COLOR), ("active", "#D1D5DB")],
        )
        
        # Estilo escuro para o Combobox do TTK
        style.configure(
            "TCombobox",
            fieldbackground="#161822",
            background="#1F2937",
            foreground=TEXT_COLOR,
            arrowcolor=TEXT_COLOR,
            bordercolor="#1F2232",
            lightcolor="#1F2232",
            darkcolor="#1F2232"
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", "#161822")],
            foreground=[("readonly", TEXT_COLOR)],
            selectbackground=[("readonly", "#2563EB")],
            selectforeground=[("readonly", TEXT_COLOR)]
        )
        
        # Configura as cores do menu dropdown suspenso
        self.root.option_add("*TCombobox*Listbox.background", "#161822")
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT_COLOR)
        
        # Estilo para Scrollbar do TTK
        style.configure(
            "Vertical.TScrollbar",
            gripcount=0,
            background="#1F2937",
            troughcolor="#0E111C",
            bordercolor="#1F2232",
            lightcolor="#1F2232",
            darkcolor="#1F2232",
            arrowsize=0
        )
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#2563EB")
        self.root.option_add("*TCombobox*Listbox.selectForeground", TEXT_COLOR)
        self.root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 9))

    def create_widgets(self):
        # 1. HEADER / CABEÇALHO UNIFICADO NO TOPO DA JANELA
        header_frame = tk.Frame(self.root, bg=BG_COLOR, pady=6)
        header_frame.pack(fill="x", padx=15, pady=(5, 0))
        
        title_label = tk.Label(
            header_frame, 
            text=" 🎥 Painel Câmeras Farmácia", 
            font=("Segoe UI", 18, "bold"), 
            fg=TEXT_COLOR, 
            bg=BG_COLOR
        )
        title_label.pack(side="left")
        
        subtitle_label = tk.Label(
            header_frame, 
            text=f"v{VERSION}", 
            font=("Segoe UI", 10, "bold"), 
            fg=ACCENT_COLOR, 
            bg=BG_COLOR
        )
        subtitle_label.pack(side="left", padx=10, pady=8)
        
        # Divisor horizontal neon premium (glow azul sutil)
        glow_line = tk.Frame(self.root, bg="#3B82F6", height=2)
        glow_line.pack(fill="x", padx=15, pady=(2, 6))

        # 1.5. CABEÇALHO DE STATUS DO TOPO (TOP STATUS HEADER)
        self.top_status_bar = tk.Frame(self.root, bg=BG_COLOR)
        self.top_status_bar.pack(fill="x", padx=15, pady=(0, 6))
        
        # Pílula 1: Status de Gravação
        self.hdr_pill_grav = tk.Label(
            self.top_status_bar, 
            text="  NVR STATUS: VERIFICANDO  ", 
            font=("Segoe UI", 8, "bold"), 
            fg=ORANGE_COLOR, 
            bg="#78350F",
            relief="flat",
            bd=0
        )
        self.hdr_pill_grav.pack(side="left", padx=(0, 6))
        
        # Pílula 2: Câmeras Online
        self.hdr_pill_cams = tk.Label(
            self.top_status_bar, 
            text="  CÂMERAS: 0/2 ONLINE  ", 
            font=("Segoe UI", 8, "bold"), 
            fg=ORANGE_COLOR, 
            bg="#78350F",
            relief="flat",
            bd=0
        )
        self.hdr_pill_cams.pack(side="left", padx=6)
        
        # Pílula 3: Espaço no Disco
        self.hdr_pill_disk = tk.Label(
            self.top_status_bar, 
            text="  DISCO: VERIFICANDO  ", 
            font=("Segoe UI", 8, "bold"), 
            fg=ORANGE_COLOR, 
            bg="#78350F",
            relief="flat",
            bd=0
        )
        self.hdr_pill_disk.pack(side="left", padx=6)
        
        # Pílula 4: Energia AC/Bateria
        self.hdr_pill_power = tk.Label(
            self.top_status_bar, 
            text="  ENERGIA: AC LINE (OK)  ", 
            font=("Segoe UI", 8, "bold"), 
            fg=GREEN_COLOR, 
            bg="#064E3B",
            relief="flat",
            bd=0
        )
        self.hdr_pill_power.pack(side="left", padx=6)

        self.hdr_pill_brain = tk.Label(
            self.top_status_bar,
            text="  ANALISE: COLETANDO  ",
            font=("Segoe UI", 8, "bold"),
            fg=ORANGE_COLOR,
            bg="#78350F",
            relief="flat",
            bd=0,
        )
        self.hdr_pill_brain.pack(side="right", padx=(6, 0))

        self.main_notebook = ttk.Notebook(self.root, style="Main.TNotebook")
        self.main_notebook.pack(fill="both", expand=True, padx=10, pady=(1, 5))
        self.main_notebook.enable_traversal()
        self.camera_page = tk.Frame(self.main_notebook, bg=BG_COLOR)
        self.analytics_page = tk.Frame(self.main_notebook, bg=BG_COLOR)
        self.main_notebook.add(self.camera_page, text="  Câmeras  ")
        self.main_notebook.add(self.analytics_page, text="  Análises  ")
        self.main_notebook.bind("<<NotebookTabChanged>>", self.on_main_tab_changed)

        self.analytics_placeholder = tk.Frame(self.analytics_page, bg=BG_COLOR)
        self.analytics_placeholder.pack(fill="both", expand=True)
        tk.Label(
            self.analytics_placeholder,
            text="Análises operacionais",
            font=("Segoe UI", 18, "bold"),
            fg=TEXT_COLOR,
            bg=BG_COLOR,
        ).pack(pady=(80, 8))
        self.lbl_analytics_placeholder = tk.Label(
            self.analytics_placeholder,
            text="Preparando histórico, rede e visão local...",
            font=("Segoe UI", 10),
            fg=TEXT_MUTED,
            bg=BG_COLOR,
        )
        self.lbl_analytics_placeholder.pack()

        # Container principal dividido em duas colunas (Esquerda e Direita)
        split_container = tk.Frame(self.camera_page, bg=BG_COLOR)
        split_container.pack(fill="both", expand=True, pady=5)
        
        left_col = tk.Frame(split_container, bg=BG_COLOR, width=430)
        left_col.pack(side="left", fill="both", expand=False)
        left_col.pack_propagate(False)
        
        right_col = tk.Frame(split_container, bg=BG_COLOR)
        right_col.pack(side="right", fill="both", expand=True)

        # 2. CARDS GLOBAIS (SERVIÇOS E REDE)
        top_cards_frame = tk.Frame(left_col, bg=BG_COLOR, pady=6)
        top_cards_frame.pack(fill="x", padx=12)
        
        # Card 1: Serviços Globais com contorno sutil
        card_global_wrapper = tk.Frame(top_cards_frame, bg=BG_COLOR, bd=0)
        card_global_wrapper.pack(fill="x", expand=True, padx=4, pady=4)
        
        # Stripe lateral (azul elétrico para serviços)
        accent_global = tk.Frame(card_global_wrapper, bg="#3B82F6", width=4)
        accent_global.pack(side="left", fill="y")
        
        self.card_global = tk.Frame(
            card_global_wrapper, 
            bg=CARD_COLOR, 
            bd=0, 
            highlightbackground="#1F2232", 
            highlightthickness=1, 
            padx=15, 
            pady=10
        )
        self.card_global.pack(side="left", fill="x", expand=True)
        self.setup_card_hover_glow(self.card_global, "#3B82F6")
        
        # Rótulo de Alerta se o HD for desconectado (inicialmente oculto)
        self.lbl_alerta_hd = tk.Label(
            left_col,
            text="⚠️ ALERTA: HD EXTERNO DESCONECTADO!\nGravando temporariamente no PC local.",
            font=("Segoe UI", 9, "bold"),
            fg=RED_COLOR,
            bg="#2D1111",
            bd=1,
            relief="solid",
            padx=10,
            pady=6
        )
        
        tk.Label(self.card_global, text="⚡ Status dos Serviços", font=("Segoe UI", 10, "bold"), fg=TEXT_COLOR, bg=CARD_COLOR).pack(anchor="w", pady=(0, 4))
        
        # Linha Ponte RTSP
        row_go2rtc = tk.Frame(self.card_global, bg=CARD_COLOR, pady=1)
        row_go2rtc.pack(anchor="w")
        self.led_go2rtc = StatusLED(row_go2rtc, size=10, bg_color=CARD_COLOR)
        self.led_go2rtc.pack(side="left", padx=(0, 6), pady=4)
        tk.Label(row_go2rtc, text="Ponte RTSP: ", font=("Segoe UI", 9), fg=TEXT_MUTED, bg=CARD_COLOR).pack(side="left")
        self.lbl_val_go2rtc = tk.Label(row_go2rtc, text="Verificando...", font=("Segoe UI", 9, "bold"), fg=ORANGE_COLOR, bg=CARD_COLOR)
        self.lbl_val_go2rtc.pack(side="left")
        
        # Linha HD FARMACIA
        row_gdrive = tk.Frame(self.card_global, bg=CARD_COLOR, pady=1)
        row_gdrive.pack(anchor="w")
        self.led_gdrive = StatusLED(row_gdrive, size=10, bg_color=CARD_COLOR)
        self.led_gdrive.pack(side="left", padx=(0, 6), pady=4)
        tk.Label(row_gdrive, text="HD FARMACIA: ", font=("Segoe UI", 9), fg=TEXT_MUTED, bg=CARD_COLOR).pack(side="left")
        self.lbl_val_gdrive = tk.Label(row_gdrive, text="Verificando...", font=("Segoe UI", 9, "bold"), fg=ORANGE_COLOR, bg=CARD_COLOR)
        self.lbl_val_gdrive.pack(side="left")
        
        # Linha Backups Locais Pendentes
        row_backups = tk.Frame(self.card_global, bg=CARD_COLOR, pady=1)
        row_backups.pack(anchor="w")
        self.led_backups = StatusLED(row_backups, size=10, bg_color=CARD_COLOR)
        self.led_backups.pack(side="left", padx=(0, 6), pady=4)
        tk.Label(row_backups, text="Backups Pendentes: ", font=("Segoe UI", 9), fg=TEXT_MUTED, bg=CARD_COLOR).pack(side="left")
        self.lbl_val_backups = tk.Label(row_backups, text="Calculando...", font=("Segoe UI", 9, "bold"), fg=ORANGE_COLOR, bg=CARD_COLOR)
        self.lbl_val_backups.pack(side="left")

        # 3. GRID DINÂMICO DE CÂMERAS
        self.cameras_main_frame = tk.Frame(left_col, bg=BG_COLOR)
        self.cameras_main_frame.pack(fill="x", padx=12, pady=4)
        
        # Layout vertical (evita esmagamento lateral de textos longos)
        self.camera_cards = {}
        
        for idx, stream in enumerate(self.streams):
            # Wrapper
            card_wrapper = tk.Frame(self.cameras_main_frame, bg=BG_COLOR, bd=0)
            card_wrapper.pack(side="top", fill="x", pady=3)
            
            # Stripe lateral (verde esmeralda para câmeras)
            accent_bar = tk.Frame(card_wrapper, bg="#10B981", width=4)
            accent_bar.pack(side="left", fill="y")
            
            # Card principal com contorno e padding aprimorado
            card = tk.Frame(
                card_wrapper, 
                bg=CARD_COLOR, 
                bd=0, 
                highlightbackground="#1F2232", 
                highlightthickness=1, 
                padx=15, 
                pady=7
            )
            card.pack(side="left", fill="x", expand=True)
            self.setup_card_hover_glow(card, "#10B981")
            
            # Título da Câmera
            cam_label = f"CÂMERA {idx+1}: {stream.upper()}"
            lbl_title = tk.Label(
                card,
                text=f"📷 {cam_label}",
                font=("Segoe UI", 10, "bold"),
                fg=ACCENT_COLOR,
                bg=CARD_COLOR,
            )
            lbl_title.pack(anchor="w", pady=(0, 4))
            
            # Novo Grid de Status (Pílulas/Badges)
            grid_frame = tk.Frame(card, bg=CARD_COLOR, pady=3)
            grid_frame.pack(fill="x", pady=(1, 3))
            grid_frame.columnconfigure((0, 1, 2), weight=1)
            
            # Coluna 0: Sinal
            col_sinal = tk.Frame(grid_frame, bg=CARD_COLOR)
            col_sinal.grid(row=0, column=0, sticky="nsew")
            tk.Label(col_sinal, text="SINAL", font=("Segoe UI", 7, "bold"), fg=TEXT_MUTED, bg=CARD_COLOR).pack(anchor="center", pady=(0, 2))
            
            sinal_badge_frame = tk.Frame(col_sinal, bg=CARD_COLOR)
            sinal_badge_frame.pack(anchor="center")
            led_sinal = StatusLED(sinal_badge_frame, size=6, bg_color=CARD_COLOR)
            led_sinal.pack(side="left", padx=(0, 4))
            lbl_sinal = tk.Label(sinal_badge_frame, text="VERIFICANDO", font=("Segoe UI", 8, "bold"), fg=ORANGE_COLOR, bg="#78350F", padx=6, pady=2)
            lbl_sinal.pack(side="left")
            
            # Coluna 1: Gravação
            col_grav = tk.Frame(grid_frame, bg=CARD_COLOR)
            col_grav.grid(row=0, column=1, sticky="nsew")
            tk.Label(col_grav, text="GRAVAÇÃO", font=("Segoe UI", 7, "bold"), fg=TEXT_MUTED, bg=CARD_COLOR).pack(anchor="center", pady=(0, 2))
            
            grav_badge_frame = tk.Frame(col_grav, bg=CARD_COLOR)
            grav_badge_frame.pack(anchor="center")
            led_grav = StatusLED(grav_badge_frame, size=6, bg_color=CARD_COLOR)
            led_grav.pack(side="left", padx=(0, 4))
            lbl_grav = tk.Label(grav_badge_frame, text="VERIFICANDO", font=("Segoe UI", 8, "bold"), fg=ORANGE_COLOR, bg="#78350F", padx=6, pady=2)
            lbl_grav.pack(side="left")
            
            # Coluna 2: Transmissão
            col_web = tk.Frame(grid_frame, bg=CARD_COLOR)
            col_web.grid(row=0, column=2, sticky="nsew")
            tk.Label(col_web, text="TRANSMISSÃO", font=("Segoe UI", 7, "bold"), fg=TEXT_MUTED, bg=CARD_COLOR).pack(anchor="center", pady=(0, 2))
            
            web_badge_frame = tk.Frame(col_web, bg=CARD_COLOR)
            web_badge_frame.pack(anchor="center")
            led_web = StatusLED(web_badge_frame, size=6, bg_color=CARD_COLOR)
            led_web.pack(side="left", padx=(0, 4))
            lbl_web = tk.Label(web_badge_frame, text="VERIFICANDO", font=("Segoe UI", 8, "bold"), fg=ORANGE_COLOR, bg="#78350F", padx=6, pady=2)
            lbl_web.pack(side="left")

            lbl_activity = tk.Label(
                card,
                text="Coletando atividade da câmera...",
                font=("Segoe UI", 8),
                fg=TEXT_MUTED,
                bg=CARD_COLOR,
                anchor="w",
                justify="left",
            )
            lbl_activity.pack(fill="x", pady=(0, 1))
            
            # Linha divisória sutil
            divider = tk.Frame(card, bg="#1F2232", height=1)
            divider.pack(fill="x", pady=4)
            
            # Última gravação/Sync (com fonte monospace menor e visual limpo)
            lbl_sync = tk.Label(card, text="Buscando...", font=("Consolas", 8), fg=TEXT_MUTED, bg=CARD_COLOR, justify="left", wraplength=380)
            lbl_sync.pack(fill="x", pady=(0, 0), anchor="w")
            
            # Salva referências para atualização
            self.camera_cards[stream] = {
                "accent_bar": accent_bar,
                "lbl_title": lbl_title,
                "led_sinal": led_sinal,
                "lbl_sinal": lbl_sinal,
                "led_grav": led_grav,
                "lbl_grav": lbl_grav,
                "led_web": led_web,
                "lbl_web": lbl_web,
                "lbl_activity": lbl_activity,
                "lbl_sync": lbl_sync
            }

        # 4. CONTROLES / BOTÕES
        btn_frame = tk.Frame(left_col, bg=BG_COLOR, pady=6)
        btn_frame.pack(fill="x", padx=12)
        
        self.btn_action = tk.Button(
            btn_frame, 
            text=" ▶️ Iniciar Todas as Gravações", 
            font=("Segoe UI", 12, "bold"), 
            fg="#FFFFFF", 
            bg="#059669", 
            activebackground="#047857", 
            activeforeground="#FFFFFF",
            bd=0, 
            cursor="hand2",
            padx=20, 
            pady=8,
            command=self.click_iniciar
        )
        self.btn_action.pack(fill="x", padx=4, pady=4, expand=True)
        self.btn_action.bind("<Enter>", self.on_btn_action_enter)
        self.btn_action.bind("<Leave>", self.on_btn_action_leave)

        # Ações extras (sem botão de diagnóstico - agora é automático)
        actions_frame = tk.Frame(left_col, bg=BG_COLOR, pady=2)
        actions_frame.pack(fill="x", padx=12)
        
        self.btn_open_folder = tk.Button(
            actions_frame, 
            text=" 📁 Abrir Pasta de Vídeos", 
            font=("Segoe UI", 9, "bold"), 
            fg=TEXT_COLOR, 
            bg="#1F2937", 
            activebackground="#374151", 
            activeforeground=TEXT_COLOR,
            bd=0, 
            cursor="hand2",
            padx=10, 
            pady=5,
            command=self.click_abrir_pasta
        )
        self.btn_open_folder.pack(fill="x", padx=4, pady=2)
        self.setup_button_hover(self.btn_open_folder, "#1F2937", "#374151")

        # Inicialização automática
        startup_frame = tk.Frame(left_col, bg=BG_COLOR, pady=2)
        startup_frame.pack(fill="x", padx=12)
        
        self.btn_setup_startup = tk.Button(
            startup_frame, 
            text=" ⚙️ Habilitar Inicialização Automática com o Windows", 
            font=("Segoe UI", 9, "bold"), 
            fg=TEXT_COLOR, 
            bg="#1F2937", 
            activebackground="#374151", 
            activeforeground=TEXT_COLOR,
            bd=0, 
            cursor="hand2",
            padx=10, 
            pady=5,
            command=self.click_configurar_inicializacao
        )
        self.btn_setup_startup.pack(fill="x", padx=4, pady=2)
        self.setup_button_hover(self.btn_setup_startup, "#1F2937", "#374151")

        # Controle de suspensão de energia
        sleep_frame = tk.Frame(left_col, bg=BG_COLOR, pady=2)
        sleep_frame.pack(fill="x", padx=12)
        
        self.chk_prevent_sleep = tk.Checkbutton(
            sleep_frame,
            text=" 🖥️ Impedir Suspensão do PC: ATIVO " if self.prevent_sleep_var.get() else " 🖥️ Impedir Suspensão do PC: INATIVO ",
            variable=self.prevent_sleep_var,
            font=("Segoe UI", 9, "bold"),
            fg=TEXT_COLOR,
            bg="#1F2937",
            activebackground="#374151",
            activeforeground=TEXT_COLOR,
            selectcolor="#064E3B",
            indicatoron=False,
            relief="flat",
            bd=0,
            padx=12,
            pady=6,
            cursor="hand2",
            command=self.toggle_prevent_sleep
        )
        self.chk_prevent_sleep.pack(fill="x", padx=4, pady=4)
        # Wrapper de configurações
        settings_wrapper = tk.Frame(left_col, bg=BG_COLOR, bd=0)
        settings_wrapper.pack(fill="x", padx=12, pady=4)
        
        # Stripe lateral (laranja/amber para configurações)
        accent_settings = tk.Frame(settings_wrapper, bg="#F59E0B", width=4)
        accent_settings.pack(side="left", fill="y")
        
        self.card_settings = tk.Frame(
            settings_wrapper, 
            bg=CARD_COLOR, 
            bd=0, 
            highlightbackground="#1F2232", 
            highlightthickness=1, 
            padx=15, 
            pady=10
        )
        self.card_settings.pack(side="left", fill="x", expand=True)
        self.setup_card_hover_glow(self.card_settings, "#F59E0B")
        tk.Label(self.card_settings, text="⚙️ Configurações do NVR", font=("Segoe UI", 10, "bold"), fg=TEXT_COLOR, bg=CARD_COLOR).pack(anchor="w", pady=(0, 6))
        
        # Linha Pasta do HD
        row_path = tk.Frame(self.card_settings, bg=CARD_COLOR, pady=2)
        row_path.pack(fill="x", anchor="w")
        tk.Label(row_path, text="Pasta do HD:  ", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=CARD_COLOR).pack(side="left")
        
        self.entry_path = tk.Entry(
            row_path, 
            bg="#161822", 
            fg=TEXT_COLOR, 
            font=("Segoe UI", 9), 
            bd=0, 
            highlightthickness=1, 
            highlightbackground="#374151", 
            highlightcolor="#3B82F6", 
            relief="flat",
            width=22, 
            insertbackground=TEXT_COLOR
        )
        self.entry_path.insert(0, GDRIVE_ROOT)
        self.entry_path.pack(side="left", padx=5, pady=2)
        
        self.btn_save_path = tk.Button(
            row_path,
            text="Salvar",
            font=("Segoe UI", 8, "bold"),
            fg=TEXT_COLOR,
            bg="#2563EB",
            activebackground="#1D4ED8",
            activeforeground=TEXT_COLOR,
            bd=0,
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=3,
            command=self.click_salvar_caminho
        )
        self.btn_save_path.pack(side="left", padx=2)
        self.setup_button_hover(self.btn_save_path, "#2563EB", "#1D4ED8")
        
        # Linha Bloco de Vídeo
        row_block = tk.Frame(self.card_settings, bg=CARD_COLOR, pady=2)
        row_block.pack(fill="x", anchor="w", pady=(6, 0))
        tk.Label(row_block, text="Bloco de Vídeo:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=CARD_COLOR).pack(side="left")
        
        self.combo_block = ttk.Combobox(row_block, values=["10 min", "15 min", "30 min"], state="readonly", width=8)
        intervalo_atual = CONFIG.get("bloco_minutos", 30)
        self.combo_block.set(f"{intervalo_atual} min")
        self.combo_block.pack(side="left", padx=5)
        
        self.btn_save_interval = tk.Button(
            row_block,
            text="Salvar",
            font=("Segoe UI", 8, "bold"),
            fg=TEXT_COLOR,
            bg="#2563EB",
            activebackground="#1D4ED8",
            activeforeground=TEXT_COLOR,
            bd=0,
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=3,
            command=self.click_salvar_intervalo
        )
        self.btn_save_interval.pack(side="left", padx=2)
        self.setup_button_hover(self.btn_save_interval, "#2563EB", "#1D4ED8")
        
        # Dica visual com ícone de informação
        self.lbl_tip_retention = tk.Label(
            self.card_settings, 
            text="💡 Dica: Gravações antigas (>90 dias) são limpas automaticamente.", 
            font=("Segoe UI", 8, "italic"), 
            fg="#9CA3AF", 
            bg=CARD_COLOR,
            anchor="w"
        )
        self.lbl_tip_retention.pack(fill="x", pady=(8, 0))

        # 3.5. CONTAINERS DAS CÂMERAS AO VIVO (na coluna da direita)
        self.live_cams_container = tk.Frame(right_col, bg=BG_COLOR)
        self.live_cams_container.pack(fill="x", expand=False, padx=10, pady=4)
        
        self.camera_widgets = {}
        for stream in self.streams:
            cam_widget = LiveCameraWidget(self.live_cams_container, stream, self)
            cam_widget.pack(side="top", fill="x", expand=False, pady=4)
            self.camera_widgets[stream] = cam_widget

        # Divisor horizontal sutil entre Câmeras e Logs
        self.intelligence_band = tk.Frame(
            right_col,
            bg="#161822",
            highlightbackground="#1F2232",
            highlightthickness=1,
            padx=12,
            pady=7,
        )
        self.intelligence_band.pack(fill="x", padx=15, pady=(4, 6))

        intelligence_header = tk.Frame(self.intelligence_band, bg="#161822")
        intelligence_header.pack(fill="x")
        tk.Label(
            intelligence_header,
            text="Diagnóstico Operacional",
            font=("Segoe UI", 9, "bold"),
            fg=TEXT_COLOR,
            bg="#161822",
        ).pack(side="left")
        self.lbl_intelligence_confidence = tk.Label(
            intelligence_header,
            text="coletando sinais",
            font=("Segoe UI", 8, "bold"),
            fg=ORANGE_COLOR,
            bg="#161822",
        )
        self.lbl_intelligence_confidence.pack(side="right")
        self.lbl_intelligence_summary = tk.Label(
            self.intelligence_band,
            text="Aguardando a primeira coleta de saude.",
            font=("Segoe UI", 9, "bold"),
            fg=TEXT_COLOR,
            bg="#161822",
            justify="left",
            anchor="w",
            wraplength=780,
        )
        self.lbl_intelligence_summary.pack(fill="x", pady=(3, 1))
        self.lbl_intelligence_action = tk.Label(
            self.intelligence_band,
            text="Acao: nenhuma antes da coleta inicial.",
            font=("Segoe UI", 8),
            fg=TEXT_MUTED,
            bg="#161822",
            justify="left",
            anchor="w",
            wraplength=780,
        )
        self.lbl_intelligence_action.pack(fill="x")

        cams_logs_sep = tk.Frame(right_col, bg="#1F2232", height=1)
        cams_logs_sep.pack(fill="x", padx=15, pady=(8, 4))

        # 5. LOG DE EVENTOS (CONSOLE PREMIUM REALOCADO NA COLUNA DIREITA)
        log_title_frame = tk.Frame(right_col, bg=BG_COLOR)
        log_title_frame.pack(fill="x", padx=15, pady=(4, 0))
        tk.Label(log_title_frame, text="📝 Log de Eventos do Sistema", font=("Segoe UI", 9, "bold"), fg=TEXT_COLOR, bg=BG_COLOR).pack(anchor="w")
        
        # Log frame wrapper para contorno elegante
        log_wrapper = tk.Frame(right_col, bg=BG_COLOR)
        log_wrapper.pack(fill="x", expand=False, padx=12, pady=(2, 6))
        
        # Barra de rolagem estilizada escura
        scrollbar = ttk.Scrollbar(log_wrapper, orient="vertical")
        scrollbar.pack(side="right", fill="y")
        
        self.txt_log = tk.Text(
            log_wrapper, 
            height=12, 
            bg="#0E111C", 
            fg="#A7F3D0", 
            font=("Consolas", 9), 
            bd=0, 
            highlightbackground="#1F2232", 
            highlightthickness=1, 
            padx=10, 
            pady=8, 
            wrap="word",
            yscrollcommand=scrollbar.set
        )
        self.txt_log.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.txt_log.yview)
        self.txt_log.configure(state="disabled")
        # Configura tags de cores para diferentes tipos de mensagens (Estilo Console Moderno)
        self.txt_log.tag_configure("tag_erro", foreground="#FFA1A1", background="#2D080A")
        self.txt_log.tag_configure("tag_ok", foreground="#A7F3D0", background="#062A17")
        self.txt_log.tag_configure("tag_info", foreground="#93C5FD", background="#0A1C30")
        self.txt_log.tag_configure("tag_warn", foreground="#FCD34D", background="#2A1B02")
        self.txt_log.tag_configure("tag_default", foreground="#D1FAE5", background="#0E111C")

        # Descarrega logs gerados durante o startup/boot
        if hasattr(self, "_startup_logs"):
            logs_to_flush = self._startup_logs
            del self._startup_logs
            for msg_item in logs_to_flush:
                self._append_to_log_widget(msg_item, self.infer_log_tag(msg_item))

    # ================= LOG DE EVENTOS =================
    def _append_to_log_widget(self, msg, tag):
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted = f"[{timestamp}] {msg}\n"
        try:
            self.txt_log.configure(state="normal")
            self.txt_log.insert(tk.END, formatted, tag)
            
            # Auto-cleanup: limita o log a 200 linhas
            line_count = int(self.txt_log.index('end-1c').split('.')[0])
            if line_count > 200:
                self.txt_log.delete('1.0', f'{line_count - 200}.0')
                
            self.txt_log.see(tk.END)
            self.txt_log.configure(state="disabled")
        except Exception:
            pass

    def infer_log_tag(self, msg):
        msg_lower = msg.lower()
        if any(word in msg_lower for word in ("erro", "falha", "critico", "crítico", "excluido", "excluído")):
            return "tag_erro"
        if any(word in msg_lower for word in ("sucesso", "concluid", "concluíd", "ativo", "configurad", "resolvido")):
            return "tag_ok"
        if any(word in msg_lower for word in ("iniciando", "escaneamento", "diagnostico", "diagnóstico", "verificando", "automatic")):
            return "tag_info"
        if any(word in msg_lower for word in ("aviso", "aguardando", "tentando", "parando", "atencao", "atenção")):
            return "tag_warn"
        return "tag_default"

    def append_persistent_log_file(self, log_file_path, msg):
        os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
        if os.path.exists(log_file_path) and os.path.getsize(log_file_path) > 2 * 1024 * 1024:
            try:
                with open(log_file_path, "r", encoding="utf-8", errors="ignore") as log_file:
                    lines = log_file.readlines()
                with open(log_file_path, "w", encoding="utf-8") as log_file:
                    log_file.writelines(lines[-500:])
            except Exception:
                return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_file_path, "a", encoding="utf-8") as log_file:
            log_file.write(f"[{timestamp}] {msg}\n")

    def write_persistent_log(self, msg):
        hostname = socket.gethostname()
        local_log_path = os.path.join(LOGS_DIR, f"log_{hostname}.txt")
        try:
            self.append_persistent_log_file(local_log_path, msg)
        except Exception:
            pass

        external_queue = getattr(self, "_external_log_queue", None)
        if external_queue is not None:
            try:
                external_queue.put_nowait(msg)
            except queue.Full:
                try:
                    external_queue.get_nowait()
                    external_queue.put_nowait(msg)
                except Exception:
                    pass

    def external_log_writer_loop(self):
        hostname = socket.gethostname()
        while not getattr(self, "_shutdown_executed", False):
            try:
                msg = self._external_log_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                if not GDRIVE_ROOT or not os.path.isdir(GDRIVE_ROOT):
                    continue
                external_log_path = os.path.join(GDRIVE_ROOT, "logs_nvr", f"log_{hostname}.txt")
                self.append_persistent_log_file(external_log_path, msg)
            except Exception:
                continue

    def drain_ui_log_queue(self):
        try:
            for _ in range(100):
                try:
                    msg, tag = self._ui_log_queue.get_nowait()
                except queue.Empty:
                    break
                self._append_to_log_widget(msg, tag)
        except Exception:
            pass

        if not getattr(self, "_shutdown_executed", False):
            try:
                self.root.after(200, self.drain_ui_log_queue)
            except Exception:
                pass

    def drain_ui_control_queue(self):
        close_requested = False
        pending_actions = []
        try:
            for _ in range(8):
                try:
                    item = self._ui_control_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    action, payload = item if isinstance(item, tuple) else (item, None)
                    if action == "close_root":
                        close_requested = True
                    else:
                        pending_actions.append((action, payload))
                finally:
                    self._ui_control_queue.task_done()
        except Exception:
            pass

        if close_requested:
            self.close_tk_root()
            return
        for action, payload in pending_actions:
            try:
                if action == "wimi_ready":
                    self._on_wimi_analytics_ready(bool(payload))
                elif action == "wimi_error":
                    self.set_wimi_panel_status("error", str(payload or ""))
            except Exception as error:
                self.add_log(f"Falha ao atualizar interface WIMI: {error}", "tag_atencao")
        try:
            self.root.after(100, self.drain_ui_control_queue)
        except Exception:
            pass

    def queue_log_for_ui(self, msg, tag):
        if self.silent or not hasattr(self, "txt_log") or self.txt_log is None:
            if not hasattr(self, "_startup_logs"):
                self._startup_logs = []
            self._startup_logs.append(msg)
            if len(self._startup_logs) > STARTUP_LOG_LIMIT:
                del self._startup_logs[:-STARTUP_LOG_LIMIT]
            return

        if threading.current_thread() is threading.main_thread():
            self._append_to_log_widget(msg, tag)
            return

        try:
            self._ui_log_queue.put_nowait((msg, tag))
        except queue.Full:
            try:
                self._ui_log_queue.get_nowait()
                self._ui_log_queue.put_nowait((msg, tag))
            except Exception:
                pass

    def add_log(self, msg, tag_override=None):
        import re

        msg = str(msg)
        log_lock = getattr(self, "_log_lock", None)
        if log_lock is None:
            log_lock = threading.Lock()
            self._log_lock = log_lock

        with log_lock:
            if not hasattr(self, "_last_logged_msgs"):
                self._last_logged_msgs = {}
                self._suppressed_counts = {}

            msg_key = re.sub(r'\d{4}-\d{2}-\d{2}[_\s\-]\d{2}[-:]\d{2}([-:]\d{2})?', '[DATE]', msg)
            msg_key = re.sub(r'0x[0-9a-fA-F]+', '[HEX]', msg_key)
            now = time.time()
            last_time = self._last_logged_msgs.get(msg_key)
            if last_time is not None and now - last_time < 120:
                self._suppressed_counts[msg_key] = self._suppressed_counts.get(msg_key, 0) + 1
                return

            suppressed = self._suppressed_counts.get(msg_key, 0)
            self._last_logged_msgs[msg_key] = now
            self._suppressed_counts[msg_key] = 0
            prune_log_dedup_state(self._last_logged_msgs, self._suppressed_counts, now)

            if suppressed:
                summary = f"[DEDUPLICACAO] A mensagem anterior se repetiu {suppressed} vezes nos ultimos 2 minutos."
                self.write_persistent_log(summary)
                self.queue_log_for_ui(summary, "tag_info")

            tag = tag_override or self.infer_log_tag(msg)
            self.write_persistent_log(msg)
            self.queue_log_for_ui(msg, tag)

        if self.silent:
            try:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
            except Exception:
                pass

    def copy_link_to_clipboard(self):
        viewer_url = f"http://{self.local_ip}:1984/visualizador.html"
        self.root.clipboard_clear()
        self.root.clipboard_append(viewer_url)
        self.add_log("Link Web copiado para a área de transferência!")
        messagebox.showinfo("Copiado", f"O link {viewer_url} foi copiado com sucesso!")

    # ================= MONITOR LOOP (THREAD SEPARADA) =================
    def get_cached_streams_data(self):
        """Obtém dados da API /api/streams com cache de 3 segundos para evitar chamadas HTTP duplicadas"""
        now = time.time()
        if self._cached_streams_data is not None and (now - self._cached_streams_time) < 3.0:
            return self._cached_streams_data
        try:
            with urllib.request.urlopen("http://127.0.0.1:1984/api/streams", timeout=1.5) as conn:
                data = json.loads(conn.read().decode())
            self._cached_streams_data = data
            self._cached_streams_time = now
            return data
        except Exception:
            return self._cached_streams_data  # Retorna cache antigo em caso de erro

    def monitor_loop(self):
        global GDRIVE_ROOT
        while self.running_monitor:
            startup_ready = getattr(self, "_startup_ready", None)
            if startup_ready is not None and not startup_ready.wait(timeout=1.0):
                continue
            # 0. Verifica quedas de energia / status da bateria do PC/Nobreak
            self.check_power_status()
            
            # 1. Verifica se go2rtc está ativo (e reinicia se estiver inativo)
            go2rtc_ok = self.check_process_go2rtc()
            if not go2rtc_ok and self.running_monitor:
                self.iniciar_go2rtc()
                time.sleep(2.0)
                go2rtc_ok = self.check_process_go2rtc()
            
            # 2. Verifica se o HD Externo está conectado (ou se foi conectado agora)
            gdrive_ok = storage_path_matches_identity(
                GDRIVE_ROOT,
                CONFIG.get("storage_identity"),
            )
            if not gdrive_ok:
                gdrive_detectado = detectar_gdrive_automatico(
                    CONFIG.get("storage_identity"),
                )
                if gdrive_detectado:
                    GDRIVE_ROOT = gdrive_detectado
                    CONFIG["gdrive_root"] = GDRIVE_ROOT
                    CONFIG["storage_identity"] = get_volume_identity(GDRIVE_ROOT)
                    salvar_config(CONFIG)
                    try:
                        os.makedirs(GDRIVE_ROOT, exist_ok=True)
                    except Exception:
                        pass
                    gdrive_ok = storage_path_matches_identity(
                        GDRIVE_ROOT,
                        CONFIG.get("storage_identity"),
                    )
                    if gdrive_ok and not self.silent:
                        self.root.after(0, lambda: self.add_log(f"HD FARMACIA detectado dinamicamente em: {GDRIVE_ROOT}"))
                        self.root.after(0, lambda: self.entry_path.delete(0, tk.END))
                        self.root.after(0, lambda: self.entry_path.insert(0, GDRIVE_ROOT))
            
            # Chamada da limpeza emergencial preventiva se o HD estiver montado
            if gdrive_ok:
                try:
                    self.executar_limpeza_emergencial()
                except Exception:
                    pass
            
            # Watchdog de Threads: reinicia a thread de gravação se deveria estar ativa mas morreu
            for idx, stream in enumerate(self.streams):
                if self.recording_active.get(stream, False):
                    t = self.recording_threads.get(stream)
                    if t is None or not t.is_alive():
                        self.add_log(f"[HEALTH][WARNING][THREAD_WATCHDOG] Thread da camera {stream.upper()} parou; reiniciando.", "tag_warn")
                        new_t = threading.Thread(
                            target=self.record_stream_thread, 
                            args=(stream, idx), 
                            daemon=True
                        )
                        self.recording_started_at[stream] = time.time()
                        self.stream_bytes_written[stream] = 0
                        self.stream_last_data_at.pop(stream, None)
                        self.recording_threads[stream] = new_t
                        new_t.start()
            
            # 3. Coleta visualizadores ao vivo
            live_viewers = self.get_live_viewers(go2rtc_ok)
            
            # 3.5. Coleta estatísticas de backups locais (com cache de 30s)
            backup_count, backup_size = self.get_backup_stats()
            
            # 4. Verifica status de cada câmera individualmente
            cam_states = {}
            for idx, stream in enumerate(self.streams):
                lock_file = f"gravando_{stream}.lock"
                log_file = f"{stream}_erros.log"
                gdrive_dir = self.get_gdrive_dir(stream, idx)
                
                c_grav_ok = self.check_process_recorder(lock_file, stream)
                c_signal_str = self.check_rtsp_stream(go2rtc_ok, stream)
                connectivity = self.evaluate_camera_connectivity(
                    go2rtc_ok,
                    stream,
                    c_signal_str,
                )
                last_file_str = self.check_last_recording(gdrive_ok, gdrive_dir, stream)
                
                # Checa por erro de duplicidade nos logs se o gravador estiver parado
                duplicate_msg = None
                if not c_grav_ok:
                    duplicate_msg = self.check_log_for_duplicate_error(os.path.join(LOGS_DIR, log_file))
                    if duplicate_msg and stream not in self.alerted_duplicates:
                        self.alerted_duplicates[stream] = True
                        if not self.silent:
                            self.root.after(0, lambda m=duplicate_msg: messagebox.showwarning("Aviso de Rede", m))
                else:
                    if stream in self.alerted_duplicates:
                        del self.alerted_duplicates[stream]
                
                web_status, web_color, web_border = self.check_live_stream_status(go2rtc_ok, stream)
                cam_states[stream] = {
                    "grav_ok": c_grav_ok,
                    "signal": c_signal_str,
                    "connectivity": connectivity,
                    "sync": last_file_str,
                    "duplicate_error": duplicate_msg is not None,
                    "web_status": web_status,
                    "web_color": web_color,
                    "web_border": web_border
                }
            
            # Atualiza a interface (se não estiver em modo silencioso)
            if not self.silent:
                self.root.after(0, self.update_ui_states, go2rtc_ok, gdrive_ok, live_viewers, cam_states, backup_count, backup_size)

            self._last_go2rtc_ok = go2rtc_ok
            self.trigger_health_assessment()
            
            # Dorme por 3 segundos
            time.sleep(3)

    def get_backup_stats(self):
        """Retorna contagem e tamanho dos backups locais pendentes com cache de 30 segundos"""
        now = time.time()
        if (now - self._cached_backup_time) < 30.0:
            return self._cached_backup_stats
        
        backup_dir = os.path.join(PROJ_DIR, "sistema", "backup_gravacoes")
        if not os.path.exists(backup_dir):
            self._cached_backup_stats = (0, 0)
            self._cached_backup_time = now
            return 0, 0
        total_files = 0
        total_size = 0
        try:
            for root_dir, _, files in os.walk(backup_dir):
                for f in files:
                    if f.endswith((".mp4", ".ts")):
                        total_files += 1
                        total_size += os.path.getsize(os.path.join(root_dir, f))
        except Exception:
            pass
        self._cached_backup_stats = (total_files, total_size)
        self._cached_backup_time = now
        return total_files, total_size

    def make_health_issue(self, code, severity, summary, evidence, action, stream=None):
        issue = {
            "code": code,
            "severity": severity,
            "summary": summary,
            "evidence": evidence,
            "action": action,
        }
        if stream:
            issue["stream"] = stream
        issue["key"] = f"{code}:{stream or 'system'}"
        return issue

    def find_latest_video_mtime(self, directories):
        latest_mtime = None
        for directory in directories:
            if not directory or not os.path.isdir(directory):
                continue
            try:
                entries = list(os.scandir(directory))
            except Exception:
                continue

            candidate_dirs = [entry.path for entry in entries if entry.is_dir(follow_symlinks=False)]
            candidate_dirs = sorted(candidate_dirs, reverse=True)[:3]
            candidate_dirs.append(directory)
            for candidate_dir in candidate_dirs:
                try:
                    for entry in os.scandir(candidate_dir):
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        if not entry.name.lower().endswith((".ts", ".mp4")):
                            continue
                        file_mtime = entry.stat(follow_symlinks=False).st_mtime
                        if latest_mtime is None or file_mtime > latest_mtime:
                            latest_mtime = file_mtime
                except Exception:
                    continue
        return latest_mtime

    def get_pending_backup_details(self, max_files=5000):
        backup_root = os.path.join(PROJ_DIR, "sistema", "backup_gravacoes")
        count = 0
        total_size = 0
        oldest_mtime = None
        truncated = False
        try:
            for root_dir, _, files in os.walk(backup_root):
                for filename in files:
                    if not filename.lower().endswith((".ts", ".mp4")):
                        continue
                    filepath = os.path.join(root_dir, filename)
                    try:
                        stat = os.stat(filepath)
                    except Exception:
                        continue
                    count += 1
                    total_size += stat.st_size
                    if oldest_mtime is None or stat.st_mtime < oldest_mtime:
                        oldest_mtime = stat.st_mtime
                    if count >= max_files:
                        truncated = True
                        return {
                            "count": count,
                            "size_bytes": total_size,
                            "oldest_mtime": oldest_mtime,
                            "truncated": truncated,
                        }
        except Exception:
            pass
        return {
            "count": count,
            "size_bytes": total_size,
            "oldest_mtime": oldest_mtime,
            "truncated": truncated,
        }

    def get_stale_storage_artifacts(self, older_than_seconds=3600, max_files=2000):
        roots = [
            os.path.join(PROJ_DIR, "sistema", "gravando_temp"),
            os.path.join(PROJ_DIR, "sistema", "backup_gravacoes"),
        ]
        if storage_path_matches_identity(
            GDRIVE_ROOT,
            CONFIG.get("storage_identity"),
        ):
            for index, stream_name in enumerate(self.streams):
                roots.append(
                    os.path.join(
                        self.get_gdrive_dir(stream_name, index),
                        ".gravando_temp",
                    )
                )
        suffixes = (".finalizing", ".syncing", ".recovering", ".recording")
        now = time.time()
        stale = []
        inspected = 0
        for scan_root in roots:
            if not os.path.isdir(scan_root):
                continue
            try:
                for root_dir, _, files in os.walk(scan_root):
                    for filename in files:
                        inspected += 1
                        if inspected > max_files:
                            return stale
                        if not filename.lower().endswith(suffixes):
                            continue
                        filepath = os.path.join(root_dir, filename)
                        try:
                            age = now - os.path.getmtime(filepath)
                        except Exception:
                            continue
                        if age >= older_than_seconds:
                            stale.append({"path": filepath, "age_seconds": int(age)})
            except Exception:
                continue
        return stale

    def scan_recent_kernel_144_reports(self, hours=24):
        now = time.time()
        if self._usb_report_cache is not None and now - self._usb_report_cache_time < KERNEL_REPORT_CACHE_SECONDS:
            return self._usb_report_cache

        query_hours = max(72, int(hours) + 24)
        powershell = (
            f"$start=(Get-Date).AddHours(-{query_hours});"
            "$stamps=@(Get-WinEvent -FilterHashtable "
            "@{LogName='Application';ProviderName='Windows Error Reporting';Id=1001;StartTime=$start} "
            "-ErrorAction Stop | Where-Object {"
            "$_.Message -match 'LiveKernelEvent' -and $_.Message -match '(?m)^P1:\\s*144\\s*$'"
            "} | ForEach-Object {"
            "$m=[regex]::Match($_.Message,'(?i)(?:USBXHCI|WATCHDOG)-(?<stamp>\\d{8}-\\d{4,6})[^\\s\\\\]*\\.dmp');"
            "if($m.Success){$m.Groups['stamp'].Value}"
            "} | Sort-Object -Unique);"
            "ConvertTo-Json -Compress -InputObject @($stamps)"
        )
        try:
            output = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", powershell],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            raw_stamps = json.loads(output) if output.strip() else []
            if isinstance(raw_stamps, str):
                raw_stamps = [raw_stamps]
            observed_ids = filter_kernel_144_dump_stamps(
                raw_stamps,
                datetime.now(),
                hours=query_hours,
            )
            recent_ids = filter_kernel_144_dump_stamps(
                observed_ids,
                datetime.now(),
                hours=hours,
            )

            state_path = getattr(
                self,
                "_kernel_144_state_path",
                os.path.join(LOGS_DIR, "kernel_144_baseline.json"),
            )
            state_exists = os.path.exists(state_path)
            try:
                state = load_json_file(state_path) if state_exists else {}
            except Exception:
                state = {}
                state_exists = False
            known_ids = {
                item for item in state.get("known_report_ids", [])
                if isinstance(item, str)
            }
            new_ids = sorted(set(observed_ids) - known_ids) if state_exists else []
            merged_ids = sorted(known_ids | set(observed_ids))[-128:]
            write_json_atomically(state_path, {
                "version": 1,
                "known_report_ids": merged_ids,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            })

            latest = None
            if recent_ids:
                stamp = recent_ids[-1]
                stamp_format = "%Y%m%d-%H%M%S" if len(stamp) == 15 else "%Y%m%d-%H%M"
                latest = datetime.strptime(stamp, stamp_format).isoformat(timespec="seconds")
            result = {
                "status": "ok",
                "count_24h": len(recent_ids),
                "latest": latest,
                "report_ids": recent_ids,
                "new_since_last_scan": len(new_ids),
                "checked_at": datetime.now().isoformat(timespec="seconds"),
                "source": "windows_event_log_dump_timestamp",
            }
        except Exception as error:
            result = {
                "status": "unknown",
                "count_24h": 0,
                "latest": None,
                "report_ids": [],
                "new_since_last_scan": 0,
                "checked_at": datetime.now().isoformat(timespec="seconds"),
                "source": "windows_event_log_dump_timestamp",
                "error": str(error),
            }
        self._usb_report_cache = result
        self._usb_report_cache_time = now
        return result

    def add_kernel_session_context(self, reports):
        result = dict(reports)
        persisted_new = result.get("new_since_last_scan")
        if isinstance(persisted_new, int):
            result["session_baseline_count"] = max(
                0,
                result.get("count_24h", 0) - persisted_new,
            )
            result["session_baseline_latest"] = None
            result["new_in_session"] = max(0, persisted_new)
            return result
        current_count = result.get("count_24h", 0)
        current_latest = result.get("latest")
        baseline = getattr(self, "_kernel_144_session_baseline", None)
        reliable = result.get("status") == "ok"
        if baseline is None and reliable:
            baseline = {"count": current_count, "latest": current_latest}
            self._kernel_144_session_baseline = baseline

        comparison_baseline = baseline or {
            "count": current_count,
            "latest": current_latest,
        }
        baseline_count = comparison_baseline.get("count", 0)
        baseline_latest = comparison_baseline.get("latest")
        new_session_reports = 0
        if reliable and baseline is not None:
            latest_is_new = bool(
                current_latest
                and current_latest != baseline_latest
                and (baseline_latest is None or current_latest > baseline_latest)
            )
            new_session_reports = max(0, current_count - baseline_count)
            if latest_is_new and new_session_reports == 0:
                new_session_reports = 1

        result["session_baseline_count"] = baseline_count
        result["session_baseline_latest"] = baseline_latest
        result["new_in_session"] = new_session_reports
        return result

    def get_process_memory_mb(self):
        try:
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong),
                    ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t),
                ]

            counters = PROCESS_MEMORY_COUNTERS_EX()
            counters.cb = ctypes.sizeof(counters)
            get_current_process = ctypes.windll.kernel32.GetCurrentProcess
            get_current_process.restype = wintypes.HANDLE
            process_handle = get_current_process()
            get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
            get_process_memory_info.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX),
                wintypes.DWORD,
            ]
            get_process_memory_info.restype = wintypes.BOOL
            success = get_process_memory_info(
                process_handle,
                ctypes.byref(counters),
                counters.cb,
            )
            if not success:
                return None
            return round(counters.WorkingSetSize / (1024 ** 2), 2)
        except Exception:
            return None

    def update_resource_trend(self, now, process_memory_mb, thread_count):
        samples = list(getattr(self, "_resource_samples", []))
        if process_memory_mb is not None:
            samples.append({
                "timestamp": now,
                "memory_mb": process_memory_mb,
                "thread_count": thread_count,
            })
        cutoff = now - (2 * 60 * 60)
        samples = [sample for sample in samples if sample["timestamp"] >= cutoff][-120:]
        self._resource_samples = samples

        result = {
            "sample_count": len(samples),
            "window_minutes": 0.0,
            "memory_growth_mb": 0.0,
            "thread_growth": 0,
        }
        if len(samples) < 2:
            return result

        first = samples[0]
        last = samples[-1]
        result["window_minutes"] = round((last["timestamp"] - first["timestamp"]) / 60, 1)
        result["memory_growth_mb"] = round(last["memory_mb"] - first["memory_mb"], 2)
        result["thread_growth"] = last["thread_count"] - first["thread_count"]
        return result

    def collect_health_snapshot(self):
        now = time.time()
        issues = []
        active_streams = [stream for stream in self.streams if self.recording_active.get(stream, False)]

        backup_root = os.path.join(PROJ_DIR, "sistema", "backup_gravacoes")
        local_status = garantir_limite_backup_local(backup_root)
        local_free_gb = local_status["free_bytes"] / (1024 ** 3)
        local_reserve_gb = local_status["reserve_bytes"] / (1024 ** 3)
        local_warning_gb = local_reserve_gb + max(5.0, local_reserve_gb * 0.25)
        if not local_status["ok"]:
            issues.append(self.make_health_issue(
                "LOCAL_SPACE_CRITICAL",
                "critical",
                "Espaco local abaixo da reserva segura.",
                f"{local_free_gb:.2f} GB livres; reserva configurada em {local_reserve_gb:.2f} GB.",
                "Liberar espaco fora das pastas de gravacao; nao apagar backups pendentes.",
            ))
        elif local_free_gb < local_warning_gb:
            issues.append(self.make_health_issue(
                "LOCAL_SPACE_LOW",
                "warning",
                "Espaco local se aproximando da reserva critica.",
                f"{local_free_gb:.2f} GB livres; reserva configurada em {local_reserve_gb:.2f} GB.",
                "Planejar liberacao de espaco antes de atingir a reserva.",
            ))

        hd_available = bool(GDRIVE_ROOT and os.path.isdir(GDRIVE_ROOT))
        hd_free_gb = None
        if not GDRIVE_ROOT:
            issues.append(self.make_health_issue(
                "DESTINATION_NOT_CONFIGURED",
                "warning",
                "Destino principal ainda nao foi configurado.",
                "gdrive_root esta vazio.",
                "Conectar o HD FARMACIA e salvar seu caminho no aplicativo.",
            ))
        elif not hd_available:
            severity = "critical" if active_streams and local_free_gb < 10 else "warning"
            issues.append(self.make_health_issue(
                "HD_UNAVAILABLE",
                severity,
                "HD principal indisponivel; gravacoes dependem do fallback local.",
                f"Destino configurado: {GDRIVE_ROOT}.",
                "Verificar cabo, energia, porta USB e se o volume FARMACIA esta montado.",
            ))
        else:
            try:
                _, _, hd_free = shutil.disk_usage(GDRIVE_ROOT)
                hd_free_gb = hd_free / (1024 ** 3)
                if hd_free_gb < 15:
                    issues.append(self.make_health_issue(
                        "HD_SPACE_CRITICAL",
                        "critical",
                        "HD principal com espaco criticamente baixo.",
                        f"{hd_free_gb:.2f} GB livres em {GDRIVE_ROOT}.",
                        "Revisar retencao e confirmar a copia das gravacoes antes de qualquer limpeza.",
                    ))
                elif hd_free_gb < 30:
                    issues.append(self.make_health_issue(
                        "HD_SPACE_LOW",
                        "warning",
                        "HD principal se aproximando do limite de limpeza.",
                        f"{hd_free_gb:.2f} GB livres em {GDRIVE_ROOT}.",
                        "Acompanhar o crescimento e planejar capacidade adicional.",
                    ))
            except Exception as error:
                issues.append(self.make_health_issue(
                    "HD_USAGE_UNKNOWN",
                    "warning",
                    "Nao foi possivel consultar o espaco do HD.",
                    str(error),
                    "Verificar estabilidade da conexao do disco.",
                ))

        pending_backup = self.get_pending_backup_details()
        if pending_backup["count"]:
            oldest_age = now - pending_backup["oldest_mtime"] if pending_backup["oldest_mtime"] else 0
            if oldest_age >= 15 * 60 or pending_backup["size_bytes"] >= 1024 ** 3:
                issues.append(self.make_health_issue(
                    "BACKUP_PENDING",
                    "warning",
                    "Existem gravacoes locais aguardando sincronizacao.",
                    f"{pending_backup['count']} arquivo(s), {pending_backup['size_bytes'] / (1024 ** 3):.2f} GB, mais antigo ha {oldest_age / 60:.0f} min.",
                    "Manter o HD conectado e confirmar que a fila esta diminuindo.",
                ))

        process_memory_mb = self.get_process_memory_mb()
        if process_memory_mb is not None and process_memory_mb >= 750:
            issues.append(self.make_health_issue(
                "PROCESS_MEMORY_HIGH",
                "critical" if process_memory_mb >= 1500 else "warning",
                "O processo do NVR esta usando memoria acima do esperado.",
                f"Working set atual: {process_memory_mb:.2f} MB.",
                "Observar crescimento no tempo e revisar streams, imagens e filas de log.",
            ))
        active_thread_count = threading.active_count()
        if active_thread_count >= 80:
            issues.append(self.make_health_issue(
                "THREAD_COUNT_HIGH",
                "warning",
                "O NVR possui uma quantidade elevada de threads ativas.",
                f"{active_thread_count} threads no processo.",
                "Verificar scanners, diagnosticos ou reconexoes iniciados repetidamente.",
            ))

        resource_trend = self.update_resource_trend(now, process_memory_mb, active_thread_count)
        if (
            resource_trend["window_minutes"] >= 30
            and resource_trend["memory_growth_mb"] >= 200
            and process_memory_mb is not None
            and process_memory_mb >= 300
        ):
            growth = resource_trend["memory_growth_mb"]
            issues.append(self.make_health_issue(
                "MEMORY_GROWTH_SUSPECT",
                "critical" if growth >= 500 else "warning",
                "A memoria do NVR aumentou na janela observada.",
                f"Aumento de {growth:.2f} MB em {resource_trend['window_minutes']:.0f} minutos.",
                "Observar a tendencia e revisar streams, imagens e filas antes de reiniciar o PC.",
            ))

        block_minutes = CONFIG.get("bloco_minutos", 30)
        stale_after = max(20 * 60, (block_minutes * 60) + (15 * 60))
        stream_data_metrics = {}
        connectivity_issues = active_camera_connectivity_issues(
            active_streams,
            getattr(self, "camera_connectivity_states", {}),
        )
        for issue in connectivity_issues:
            issues.append(self.make_health_issue(
                issue["code"],
                issue["severity"],
                issue["summary"],
                issue["evidence"],
                issue["action"],
                issue["stream"],
            ))
        for index, stream in enumerate(self.streams):
            if not self.recording_active.get(stream, False):
                continue
            thread_obj = self.recording_threads.get(stream)
            if thread_obj is None or not thread_obj.is_alive():
                issues.append(self.make_health_issue(
                    "RECORDING_THREAD_DEAD",
                    "critical",
                    f"A camera {stream.upper()} esta marcada como ativa, mas sua thread parou.",
                    "Thread ausente ou finalizada.",
                    "O watchdog deve reiniciar a thread; verificar o log se o problema repetir.",
                    stream,
                ))

            failures = self.reconnect_failures.get(stream, 0)
            if failures >= 3:
                issues.append(self.make_health_issue(
                    "RECONNECT_STORM",
                    "critical" if failures >= 6 else "warning",
                    f"A camera {stream.upper()} apresenta falhas consecutivas de reconexao.",
                    f"{failures} falhas consecutivas.",
                    "Verificar rede, alimentacao da camera e estabilidade do go2rtc.",
                    stream,
                ))

            storage_dirs = [os.path.join(backup_root, stream)]
            gdrive_dir = self.get_gdrive_dir(stream, index)
            if gdrive_dir:
                storage_dirs.append(gdrive_dir)
            latest_mtime = self.find_latest_video_mtime(storage_dirs)
            started_at = self.recording_started_at.get(stream, now)
            running_for = now - started_at
            bytes_written = getattr(self, "stream_bytes_written", {}).get(stream, 0)
            last_data_at = getattr(self, "stream_last_data_at", {}).get(stream)
            last_data_age = now - last_data_at if last_data_at else None
            stream_data_metrics[stream] = {
                "bytes_written_session": bytes_written,
                "last_data_age_seconds": round(last_data_age, 1) if last_data_age is not None else None,
            }
            if running_for >= 120 and (last_data_age is None or last_data_age >= 90):
                evidence = (
                    "nenhum byte recebido nesta sessao"
                    if last_data_age is None
                    else f"ultimo byte recebido ha {last_data_age:.0f} segundos"
                )
                issues.append(self.make_health_issue(
                    "STREAM_NO_DATA",
                    "critical" if last_data_age is None or last_data_age >= 300 else "warning",
                    f"A camera {stream.upper()} esta ativa, mas parou de entregar dados.",
                    evidence,
                    "Verificar sinal, rede, energia da camera e produtor do go2rtc.",
                    stream,
                ))
            if running_for >= stale_after and (latest_mtime is None or now - latest_mtime >= stale_after):
                evidence = "nenhum arquivo finalizado encontrado" if latest_mtime is None else f"ultimo arquivo finalizado ha {(now - latest_mtime) / 60:.0f} min"
                issues.append(self.make_health_issue(
                    "RECORDING_STALLED",
                    "critical",
                    f"A camera {stream.upper()} parece ativa, mas nao publica blocos recentes.",
                    evidence,
                    "Verificar stream, temporarios, reconexoes e espaco antes de reiniciar.",
                    stream,
                ))

            if self.recording_destinations.get(stream) == "backup":
                issues.append(self.make_health_issue(
                    "CAMERA_ON_FALLBACK",
                    "warning",
                    f"A camera {stream.upper()} esta gravando no fallback local.",
                    "Destino dinamico atual: backup local.",
                    "Restabelecer o HD e confirmar a sincronizacao dos blocos pendentes.",
                    stream,
                ))

        stale_artifacts = self.get_stale_storage_artifacts()
        if stale_artifacts:
            issues.append(self.make_health_issue(
                "STALE_TEMPORARIES",
                "warning",
                "Existem arquivos de publicacao temporaria antigos.",
                f"{len(stale_artifacts)} artefato(s); exemplo: {stale_artifacts[0]['path']}.",
                "Preservar os arquivos e executar a recuperacao antes de qualquer limpeza.",
            ))

        smart_snapshot = getattr(self, "_smart_snapshot", {"status": "pending"})
        if smart_snapshot.get("status") == "degraded":
            issues.append(self.make_health_issue(
                "SMART_DEGRADED",
                "critical",
                "O status basico do Windows nao retornou OK para pelo menos um disco.",
                json.dumps(smart_snapshot.get("drives", []), ensure_ascii=True),
                "Interromper manutencoes pesadas e confirmar com diagnostico SMART do fabricante.",
            ))
        elif smart_snapshot.get("status") == "unknown":
            issues.append(self.make_health_issue(
                "SMART_UNKNOWN",
                "warning",
                "A consulta basica de status dos discos nao foi conclusiva.",
                smart_snapshot.get("error") or "status desconhecido",
                "Executar diagnostico do fabricante ou consulta administrativa separada.",
            ))

        power_snapshot = getattr(self, "_power_snapshot", {"status": "unknown"})
        if power_snapshot.get("status") == "battery":
            battery_percent = power_snapshot.get("battery_percent")
            severity = "critical" if battery_percent is not None and battery_percent <= 20 else "warning"
            battery_text = "percentual desconhecido" if battery_percent is None else f"{battery_percent}% restantes"
            issues.append(self.make_health_issue(
                "POWER_ON_BATTERY",
                severity,
                "O computador esta operando em bateria ou nobreak.",
                battery_text,
                "Confirmar a energia e manter margem para o encerramento seguro.",
            ))

        kernel_reports = self.add_kernel_session_context(
            self.scan_recent_kernel_144_reports()
        )
        current_latest = kernel_reports.get("latest")
        new_session_reports = kernel_reports["new_in_session"]

        if new_session_reports:
            issues.append(self.make_health_issue(
                "KERNEL_144_NEW_SESSION",
                "critical",
                "O Windows registrou nova falha de controlador/dispositivo nesta sessao.",
                f"{new_session_reports} novo(s) Kernel_144; ultimo em {current_latest}.",
                "Evitar manutencao pesada e verificar USB, cabo, porta, energia e drivers.",
            ))
        if kernel_reports.get("status") == "ok" and kernel_reports.get("count_24h", 0):
            report_count = kernel_reports["count_24h"]
            issues.append(self.make_health_issue(
                "KERNEL_144_REPORTS",
                "critical" if report_count >= 3 else "warning",
                "O Windows registrou falhas recentes de controlador/dispositivo.",
                f"{report_count} relatorio(s) Kernel_144 nas ultimas 24h; ultimo em {kernel_reports.get('latest')}.",
                "Investigar USBXHCI, cabo, porta, energia e drivers; o aplicativo nao corrige falha fisica.",
            ))

        if active_streams and not self._last_go2rtc_ok:
            issues.append(self.make_health_issue(
                "GO2RTC_UNAVAILABLE",
                "critical",
                "A ponte go2rtc nao esta respondendo enquanto ha gravacoes ativas.",
                f"Falhas da API: {getattr(self, 'go2rtc_api_fails', 0)}.",
                "Aguardar o watchdog; se repetir, verificar logs, rede e executavel.",
            ))
        if self.go2rtc_restart_count >= 5:
            issues.append(self.make_health_issue(
                "GO2RTC_RESTART_STORM",
                "warning",
                "A ponte go2rtc reiniciou muitas vezes nesta sessao.",
                f"{self.go2rtc_restart_count} reinicios.",
                "Correlacionar com rede, cameras e eventos USB antes de reiniciar o PC.",
            ))

        severity_rank = {"healthy": 0, "warning": 1, "critical": 2}
        overall_status = "healthy"
        for issue in issues:
            if severity_rank[issue["severity"]] > severity_rank[overall_status]:
                overall_status = issue["severity"]

        snapshot = {
            "schema_version": 1,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "hostname": socket.gethostname(),
            "overall_status": overall_status,
            "issues": issues,
            "metrics": {
                "active_streams": active_streams,
                "thread_count": active_thread_count,
                "process_memory_mb": process_memory_mb,
                "local_free_gb": round(local_free_gb, 2),
                "hd_available": hd_available,
                "hd_free_gb": round(hd_free_gb, 2) if hd_free_gb is not None else None,
                "pending_backup_count": pending_backup["count"],
                "pending_backup_gb": round(pending_backup["size_bytes"] / (1024 ** 3), 3),
                "stream_data": stream_data_metrics,
                "camera_connectivity": {
                    stream: dict(state)
                    for stream, state in getattr(self, "camera_connectivity_states", {}).items()
                },
                "resource_trend": resource_trend,
                "go2rtc_restart_count": self.go2rtc_restart_count,
                "kernel_144_reports_24h": kernel_reports.get("count_24h"),
            },
            "hardware": {
                "smart": smart_snapshot,
                "kernel_144": kernel_reports,
                "power": power_snapshot,
            },
        }
        snapshot["intelligence"] = build_operational_intelligence(snapshot)
        return snapshot

    def persist_health_snapshot(self, snapshot):
        health_path = os.path.join(LOGS_DIR, "health_status.json")
        temp_path = health_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as health_file:
            json.dump(snapshot, health_file, ensure_ascii=True, indent=2)
            health_file.flush()
            try:
                os.fsync(health_file.fileno())
            except Exception:
                pass
        os.replace(temp_path, health_path)

    def report_health_transitions(self, snapshot):
        current_issues = {issue["key"]: issue for issue in snapshot["issues"]}
        current_keys = set(current_issues)
        for key in sorted(current_keys - self._health_issue_keys):
            issue = current_issues[key]
            self.add_log(
                f"[HEALTH][{issue['severity'].upper()}][{issue['code']}] {issue['summary']} "
                f"Evidencia: {issue['evidence']} Acao: {issue['action']}",
                "tag_erro" if issue["severity"] == "critical" else "tag_warn",
            )
        for key in sorted(self._health_issue_keys - current_keys):
            self.add_log(f"[HEALTH][RESOLVED] Situacao normalizada: {key}.", "tag_ok")
        self._health_issue_keys = current_keys

        intelligence = snapshot.get("intelligence") or {}
        intelligence_key = (
            intelligence.get("status"),
            intelligence.get("root_cause"),
            tuple(intelligence.get("priority_actions") or []),
        )
        if intelligence_key != getattr(self, "_intelligence_key", None):
            status = intelligence.get("status", "attention")
            self.add_log(
                f"[INTELLIGENCE][{status.upper()}][{intelligence.get('root_cause', 'unknown')}] "
                f"{intelligence.get('headline', 'Analise atualizada.')} "
                f"Confianca: {intelligence.get('confidence_score', 0)}%. "
                f"Acao: {(intelligence.get('priority_actions') or ['Revisar diagnostico.'])[0]}",
                "tag_erro" if status == "critical" else ("tag_warn" if status == "attention" else "tag_ok"),
            )
            self._intelligence_key = intelligence_key

    def run_health_assessment(self):
        if not self._health_lock.acquire(blocking=False):
            return
        try:
            snapshot = self.collect_health_snapshot()
            self.persist_health_snapshot(snapshot)
            self.report_health_transitions(snapshot)
            self._health_snapshot = snapshot
            if not self.silent:
                self.root.after(
                    0,
                    self.update_intelligence_ui,
                    snapshot.get("intelligence") or {},
                    snapshot.get("generated_at"),
                )
        except Exception as error:
            self.add_log(f"[HEALTH][WARNING][ASSESSMENT_FAILED] Falha no avaliador de saude: {str(error)}", "tag_warn")
        finally:
            self._health_lock.release()

    def trigger_health_assessment(self, force=False):
        now = time.time()
        if not force and now - self._last_health_check < HEALTH_CHECK_INTERVAL_SECONDS:
            return
        if self._health_lock.locked():
            return
        self._last_health_check = now
        threading.Thread(target=self.run_health_assessment, daemon=True).start()

    def get_health_report_lines(self):
        snapshot = self._health_snapshot
        if snapshot is None:
            try:
                snapshot = self.collect_health_snapshot()
            except Exception as error:
                return [f" - Avaliador indisponivel: {str(error)}"]

        lines = [f" - Estado geral: {snapshot['overall_status'].upper()}"]
        intelligence = snapshot.get("intelligence") or {}
        if intelligence:
            lines.append(
                f" - Analise: {intelligence.get('headline')} "
                f"(causa: {intelligence.get('root_cause')}, confianca: {intelligence.get('confidence_score')}%)"
            )
            lines.append(f"   Explicacao: {intelligence.get('explanation')}")
            for action in intelligence.get("priority_actions") or []:
                lines.append(f"   Prioridade: {action}")
        if not snapshot["issues"]:
            lines.append(" - Nenhuma situacao de risco detectada nesta coleta.")
            return lines
        for issue in snapshot["issues"]:
            lines.append(f" - [{issue['severity'].upper()}] {issue['code']}: {issue['summary']}")
            lines.append(f"   Evidencia: {issue['evidence']}")
            lines.append(f"   Acao: {issue['action']}")
        return lines

    def executar_limpeza_emergencial(self):
        """Aplica retencao aprovada apenas quando a limpeza emergencial foi habilitada."""
        if not storage_path_matches_identity(GDRIVE_ROOT, CONFIG.get("storage_identity")):
            return
            
        try:
            total, used, free = shutil.disk_usage(GDRIVE_ROOT)
            free_gb = free / (1024 ** 3)
            
            if free_gb >= 15.0:
                self._emergency_cleanup_warning_active = False
                return  # Espaço confortável

            if not CONFIG.get("emergency_cleanup_enabled", False):
                if not getattr(self, "_emergency_cleanup_warning_active", False):
                    self.add_log(
                        "[HEALTH][WARNING][HD_LOW_SPACE] HD abaixo de 15 GB; exclusao emergencial esta desativada. "
                        "A gravacao preservara o acervo e usara o fallback local se necessario.",
                        "tag_warn",
                    )
                    self._emergency_cleanup_warning_active = True
                return
                
            if not self.silent:
                self.add_log(f"🚨 [ESPAÇO CRÍTICO] Apenas {free_gb:.2f} GB livres no HD! Iniciando limpeza emergencial...")
                
            # Varre subpastas das câmeras no HD externo para achar pastas de datas (formato YYYY-MM-DD)
            pastas_data = set()
            for gdrive_dest in self.get_camera_storage_dirs():
                if os.path.exists(gdrive_dest):
                    for item in os.listdir(gdrive_dest):
                        item_path = os.path.join(gdrive_dest, item)
                        if os.path.isdir(item_path):
                            import re
                            if re.match(r'^\d{4}-\d{2}-\d{2}$', item):
                                pastas_data.add(item)
                                    
            if not pastas_data:
                if not self.silent:
                    self.add_log("⚠️ Nenhuma pasta de data encontrada para limpeza.")
                return
                
            datas_ordenadas = sorted(list(pastas_data))
            retention_days = normalize_retention_days(CONFIG.get("retention_days"))
            limite_data = (datetime.now() - timedelta(days=retention_days)).date()
            datas_deletaveis = []
            for folder_name in datas_ordenadas:
                try:
                    folder_date = datetime.strptime(folder_name, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if folder_date < limite_data:
                    datas_deletaveis.append(folder_name)
            
            if not datas_deletaveis:
                if not self.silent:
                    self.add_log(
                        f"⚠️ Nenhuma gravação mais antiga que {retention_days} dias está elegível. "
                        "Abortando exclusão por segurança."
                    )
                return
                
            for data_deletar in datas_deletaveis:
                if not self.silent:
                    self.add_log(f"🧹 Deletando gravações antigas do dia {data_deletar} para liberar espaço...")
                    
                for gdrive_dest in self.get_camera_storage_dirs():
                    pasta_dia = os.path.join(gdrive_dest, data_deletar)
                    if os.path.exists(pasta_dia):
                        try:
                            shutil.rmtree(pasta_dia)
                        except Exception as e:
                            if not self.silent:
                                self.add_log(f"Erro ao deletar {pasta_dia}: {str(e)}")
                                    
                # Reavalia
                total, used, free = shutil.disk_usage(GDRIVE_ROOT)
                free_gb = free / (1024 ** 3)
                if free_gb >= 30.0:
                    if not self.silent:
                        self.add_log(f"✅ Limpeza emergencial concluída! Espaço livre recuperado: {free_gb:.2f} GB.")
                    break
        except Exception as err:
            if not self.silent:
                self.add_log(f"Erro na limpeza emergencial: {str(err)}")

    def toggle_prevent_sleep(self):
        if self.prevent_sleep_var.get():
            self.apply_prevent_sleep(True)
            self.chk_prevent_sleep.configure(text=" 🖥️ Impedir Suspensão do PC: ATIVO ")
        else:
            self.apply_prevent_sleep(False)
            self.chk_prevent_sleep.configure(text=" 🖥️ Impedir Suspensão do PC: INATIVO ")

    def apply_prevent_sleep(self, enable):
        try:
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ES_DISPLAY_REQUIRED = 0x00000002
            
            if enable:
                # Informa ao Windows para manter o sistema ativo
                required_state = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
                if not self.silent:
                    required_state |= ES_DISPLAY_REQUIRED
                ctypes.windll.kernel32.SetThreadExecutionState(
                    required_state
                )
                if not self.silent:
                    self.add_log("🖥️ Suspensão do PC impedida automaticamente.")
            else:
                # Restaura as configurações padrão do Windows
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
                if not self.silent:
                    self.add_log("🖥️ Configurações padrão de energia restauradas.")
        except Exception as e:
            if not self.silent:
                self.add_log(f"Erro ao configurar estado de energia: {str(e)}")

    def read_power_snapshot(self):
        try:
            status = SYSTEM_POWER_STATUS()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
                ac_status = status.ACLineStatus
                battery_percent = status.BatteryLifePercent
                return {
                    "status": "battery" if ac_status == 0 else ("ac" if ac_status == 1 else "unknown"),
                    "battery_percent": None if battery_percent == 255 else int(battery_percent),
                    "checked_at": datetime.now().isoformat(timespec="seconds"),
                }
            raise Exception("GetSystemPowerStatus falhou")
        except Exception as error:
            return {
                "status": "unknown",
                "battery_percent": None,
                "error": str(error),
                "checked_at": datetime.now().isoformat(timespec="seconds"),
            }

    def check_power_status(self):
        self._power_snapshot = self.read_power_snapshot()
        power_status = self._power_snapshot.get("status")
        battery_percent = self._power_snapshot.get("battery_percent")

        if power_status == "battery":
            if not self.on_battery:
                self.on_battery = True
                self.add_log("[HEALTH][WARNING][POWER_ON_BATTERY] Queda de energia detectada; PC em bateria/nobreak.", "tag_warn")
            if battery_percent is not None and battery_percent <= 20:
                self.add_log(f"[HEALTH][CRITICAL][BATTERY_LOW] Bateria em {battery_percent}%; iniciando desligamento seguro.", "tag_erro")
                self.graceful_shutdown_due_to_power_loss()
        elif power_status == "ac" and self.on_battery:
            self.on_battery = False
            self.add_log("[HEALTH][RESOLVED] Energia eletrica restabelecida.", "tag_ok")

    def graceful_shutdown_due_to_power_loss(self):
        # 1. Avisa por voz em segundo plano
        self.speak("Queda de energia detectada. Salvando vídeos e desligando o computador para proteção.")

        # Impede que o watchdog religue a ponte durante o encerramento.
        self.running_monitor = False
        self.running_sync = False
        
        # 2. Finaliza as gravações ativas de forma limpa (salva buffers no disco)
        self.run_stop_sequence()
        analytics_stopped = self.wait_for_wimi_shutdown(attempts=1, retry_delay=0)
        
        # 3. Executa o comando de desligamento do Windows (com timer de 15s para segurança)
        try:
            subprocess.Popen("shutdown /s /t 15 /f /c \"Queda de Energia - Desligamento Seguro NVR\"", shell=True)
        except Exception:
            pass
            
        # 4. Encerra a interface apenas se os bancos locais ja foram liberados.
        # O Windows ainda concluira o desligamento fisico apos o temporizador.
        if analytics_stopped:
            self.request_tk_shutdown()

    def get_lifecycle_lock(self):
        lock = getattr(self, "_lifecycle_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._lifecycle_lock = lock
        return lock

    def managed_go2rtc_running(self):
        process = getattr(self, "_go2rtc_process", None)
        try:
            return process is not None and process.poll() is None
        except Exception:
            return False

    def stop_managed_go2rtc(self):
        with self.get_lifecycle_lock():
            self._stop_managed_go2rtc_locked()

    def _stop_managed_go2rtc_locked(self):
        """Encerra somente a ponte criada por esta instância do NVR."""
        process = getattr(self, "_go2rtc_process", None)
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        except Exception:
            pass
        finally:
            self._go2rtc_process = None
            self.go2rtc_api_fails = 0

    def check_process_go2rtc(self):
        managed_running = self.managed_go2rtc_running()
        try:
            req = urllib.request.Request("http://127.0.0.1:1984/api/streams")
            with urllib.request.urlopen(req, timeout=2) as response:
                if response.status == 200:
                    self.go2rtc_api_fails = 0
                    return True
        except Exception:
            self.go2rtc_api_fails = getattr(self, "go2rtc_api_fails", 0) + 1
            if self.go2rtc_api_fails >= 3 and managed_running:
                self.add_log("[HEALTH][WARNING][GO2RTC_WATCHDOG] Ponte RTSP sem resposta; reiniciando.", "tag_warn")
                self.stop_managed_go2rtc()
                return False

        return managed_running and self.go2rtc_api_fails < 3

    def probe_go2rtc_api(self, timeout=2.0):
        try:
            request = urllib.request.Request("http://127.0.0.1:1984/api/streams")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            stream_names = sorted(payload.keys()) if isinstance(payload, dict) else []
            return {"ok": response.status == 200, "streams": stream_names, "error": None}
        except Exception as error:
            return {"ok": False, "streams": [], "error": str(error)}

    def probe_go2rtc_recording_route(self, timeout=1.0):
        probe_url = "http://127.0.0.1:1984/api/stream.ts?src=__nvr_route_probe__"
        try:
            with urllib.request.urlopen(probe_url, timeout=timeout) as response:
                return response.status == 200
        except urllib.error.HTTPError as error:
            try:
                body = error.read(128)
            except Exception:
                body = b""
            return error.code == 404 and body.strip() == b"stream not found"
        except Exception:
            return False

    def wait_for_go2rtc_recording_route(self, timeout_seconds=8.0):
        deadline = time.monotonic() + max(0.5, float(timeout_seconds))
        while time.monotonic() < deadline:
            if self.probe_go2rtc_recording_route(timeout=1.0):
                return True
            if getattr(self, "_shutdown_executed", False) or not self.running_monitor:
                return False
            time.sleep(0.2)
        return False

    def iniciar_go2rtc(self):
        with self.get_lifecycle_lock():
            try:
                if self.managed_go2rtc_running():
                    return False
                if self.probe_go2rtc_api(timeout=0.75)["ok"]:
                    self.go2rtc_api_fails = 0
                    return False
                if not self.silent:
                    self.add_log("Ligando Ponte RTSP (go2rtc.exe)...")
                go2rtc_dir = os.path.dirname(GO2RTC_EXE)
                env = os.environ.copy()
                env["PATH"] = go2rtc_dir + os.pathsep + env.get("PATH", "")
                self._go2rtc_process = subprocess.Popen(
                    [GO2RTC_EXE],
                    cwd=go2rtc_dir,
                    env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                self.go2rtc_restart_count += 1
                return True
            except Exception as e:
                if not self.silent:
                    self.add_log(f"Erro ao iniciar go2rtc.exe: {str(e)}")
        return False

    def is_pid_running_and_python(self, pid):
        if not pid:
            return False
        try:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
                
            exit_code = ctypes.c_ulong()
            active_success = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            is_active = active_success and (exit_code.value == 259) # STILL_ACTIVE
            
            size = ctypes.c_ulong(1024)
            buf = ctypes.create_unicode_buffer(1024)
            exe_success = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
            kernel32.CloseHandle(handle)
            
            if is_active and exe_success:
                exe_name = os.path.basename(buf.value).lower()
                if "python" in exe_name:
                    return True
        except Exception:
            pass
        return False

    def is_pid_owned_recorder(self, pid, lock_data=None):
        if not self.is_pid_running_and_python(pid):
            return False
        try:
            output = subprocess.check_output(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        f"Get-CimInstance Win32_Process -Filter \"ProcessId = {int(pid)}\" "
                        "| Select-Object ExecutablePath,CommandLine | ConvertTo-Json -Compress"
                    ),
                ],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            process_data = json.loads(output)
            command_line = str(process_data.get("CommandLine") or "")
            normalized_command = command_line.replace("/", "\\").casefold()
            expected_script = os.path.abspath(__file__).replace("/", "\\").casefold()
            return expected_script in normalized_command
        except Exception:
            return False

    def check_process_recorder(self, lock_filename, stream_name):
        if self.recording_active.get(stream_name, False):
            t = self.recording_threads.get(stream_name)
            if t is not None and t.is_alive():
                return True
            
        lock_path = os.path.join(LOGS_DIR, lock_filename)
        if not os.path.exists(lock_path):
            return False
        try:
            with open(lock_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content.isdigit():
                lock_data = {"pid": int(content), "legacy": True}
            else:
                lock_data = json.loads(content)
                if not isinstance(lock_data, dict) or not isinstance(lock_data.get("pid"), int):
                    return False
            pid = lock_data["pid"]
            
            if pid == os.getpid():
                return False
                
            return self.is_pid_owned_recorder(pid, lock_data)
        except Exception:
            try:
                output = subprocess.check_output(
                    f'wmic process where "CommandLine like \'%gerenciador.pyw%\' and not CommandLine like \'%wmic%\'" get ProcessId',
                    shell=True,
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                pids = [line.strip() for line in output.split('\n') if line.strip().isdigit()]
                return len(pids) > 0
            except Exception:
                return False

    def check_rtsp_stream(self, go2rtc_ok, stream_name):
        if not go2rtc_ok:
            return "Indisponível"
        try:
            data = self.get_cached_streams_data()
            if data is None:
                return "Erro API"
            if stream_name in data:
                producers = data[stream_name].get("producers") or []
                if any(producer_has_media_evidence(producer) for producer in producers):
                    return "Sinal OK"
                elif producers:
                    return "Conectando..."
                else:
                    return "Sem produtor"
            else:
                return "Não configurada"
        except Exception:
            return "Erro API"

    def evaluate_camera_connectivity(self, go2rtc_ok, stream_name, signal_hint):
        now = time.time()
        previous = self.camera_connectivity_states.get(stream_name, {})
        samples = self.camera_signal_samples.setdefault(
            stream_name,
            {"missing": 0, "success": 0},
        )
        recording_active = self.recording_active.get(stream_name, False)
        last_data_at = self.stream_last_data_at.get(stream_name)
        widget = getattr(self, "camera_widgets", {}).get(stream_name)
        preview_last_frame_at = getattr(widget, "last_frame_at", None)
        preview_active = bool(getattr(widget, "running", False))
        recorder_fresh = (
            last_data_at is not None
            and now - last_data_at <= CAMERA_DATA_FRESH_SECONDS
        )
        preview_fresh = (
            preview_last_frame_at is not None
            and now - preview_last_frame_at <= CAMERA_DATA_FRESH_SECONDS
        )
        producer_active = "Sinal OK" in signal_hint
        positive_sample = go2rtc_ok and (
            recorder_fresh
            or preview_fresh
            or (not recording_active and producer_active)
        )
        observation_active = recording_active or preview_active or producer_active
        update_camera_signal_samples(samples, positive_sample, observation_active)

        result = classify_camera_connectivity(
            go2rtc_ok=go2rtc_ok,
            producer_active=producer_active,
            recording_active=recording_active,
            recording_started_at=self.recording_started_at.get(stream_name),
            last_data_at=last_data_at,
            preview_last_frame_at=preview_last_frame_at,
            preview_active=preview_active,
            reconnect_failures=self.reconnect_failures.get(stream_name, 0),
            missing_samples=samples["missing"],
            success_samples=samples["success"],
            previous_status=previous.get("status"),
            now=now,
        )
        result = enrich_camera_connectivity_state(previous, result, now)
        result.update({
            "producer_active": producer_active,
            "recording_active": recording_active,
            "missing_samples": samples["missing"],
            "success_samples": samples["success"],
        })
        self.camera_connectivity_states[stream_name] = result

        previous_status = previous.get("status")
        if previous_status and previous_status != result["status"]:
            self._last_health_check = 0.0
            if result["status"] == "online":
                self.add_log(
                    f"[HEALTH][RECOVERY][CAMERA_ONLINE] {stream_name.upper()} voltou a entregar midia.",
                    "tag_success",
                )
            elif result["status"] == "offline":
                self.add_log(
                    f"[HEALTH][CRITICAL][CAMERA_OFFLINE] {stream_name.upper()} ficou offline. Evidencia: {result['reason']}.",
                    "tag_error",
                )
            else:
                self.add_log(
                    f"[HEALTH][WARNING][CAMERA_RECONNECTING] {stream_name.upper()} esta reconectando. Evidencia: {result['reason']}.",
                    "tag_warn",
                )
        return result

    def check_live_stream_status(self, go2rtc_ok, stream_name):
        if not go2rtc_ok:
            return "Indisponível", RED_COLOR, "#991B1B"
        
        live_name = stream_name + "_live"
        try:
            data = self.get_cached_streams_data()
            if data is None:
                return "Erro API", RED_COLOR, "#991B1B"
            if live_name in data:
                stream_data = data[live_name]
                consumers = stream_data.get("consumers") or []
                producers = stream_data.get("producers") or []
                
                # Filtra consumidores internos (ignora Python/FFmpeg)
                real_consumers = []
                for c in consumers:
                    ua = c.get("user_agent", "").lower()
                    if "python" not in ua and "lavf" not in ua:
                        real_consumers.append(c)
                        
                if real_consumers:
                    # Se há pessoas assistindo, verifica se o transcoding está ativo
                    has_active_producer = False
                    for p in producers:
                        if p.get("tracks") or p.get("mediainfo") or p.get("active"):
                            has_active_producer = True
                            break
                    
                    if has_active_producer:
                        return f"ATIVA ({len(real_consumers)})", GREEN_COLOR, "#065F46"
                    else:
                        return "ERRO TRANSCOD.", RED_COLOR, "#991B1B"
                else:
                    return "DISPONÍVEL", GREEN_COLOR, "#065F46"
            else:
                return "Não configurada", ORANGE_COLOR, "#78350F"
        except Exception:
            return "Erro API", RED_COLOR, "#991B1B"

    def scan_latest_recording(self, read_path):
        last_file = None
        last_mtime = None
        for root_dir, _, files in os.walk(read_path):
            for filename in files:
                if not filename.lower().endswith((".mp4", ".ts")):
                    continue
                filepath = os.path.join(root_dir, filename)
                try:
                    mtime = os.path.getmtime(filepath)
                except OSError:
                    continue
                if last_mtime is None or mtime > last_mtime:
                    last_file = filepath
                    last_mtime = mtime
        return last_file, last_mtime

    def check_last_recording(self, gdrive_ok, gdrive_path, stream_name):
        read_path = gdrive_path
        if not gdrive_ok or not os.path.exists(gdrive_path):
            read_path = os.path.join(PROJ_DIR, "sistema", "backup_gravacoes", stream_name)
            
        if not os.path.exists(read_path):
            return "Nenhuma gravação encontrada."
            
        try:
            now_monotonic = time.monotonic()
            cache = getattr(self, "_last_recording_cache", None)
            if cache is None:
                cache = {}
                self._last_recording_cache = cache
            normalized_path = os.path.normcase(os.path.abspath(read_path))
            cached = cache.get(stream_name)
            if (
                cached
                and cached.get("read_path") == normalized_path
                and now_monotonic - cached.get("checked_at", 0) < 30
            ):
                last_file = cached.get("last_file")
                mtime = cached.get("mtime")
            else:
                last_file, mtime = self.scan_latest_recording(read_path)
                cache[stream_name] = {
                    "read_path": normalized_path,
                    "checked_at": now_monotonic,
                    "last_file": last_file,
                    "mtime": mtime,
                }

            if not last_file or mtime is None:
                return "Sem gravações nesta pasta."

            mtime_dt = datetime.fromtimestamp(mtime)
            delta = datetime.now() - mtime_dt
            
            if delta.total_seconds() < 60:
                tempo = "agora mesmo"
            elif delta.total_seconds() < 3600:
                tempo = f"há {int(delta.total_seconds() // 60)} min"
            else:
                tempo = f"há {int(delta.total_seconds() // 3600)}h e {int((delta.total_seconds() % 3600) // 60)}min"
                
            filename = os.path.basename(last_file)
            origem = "HD" if read_path == gdrive_path else "PC Local"
            return f"{filename}\n({origem} | Sincronizado: {tempo} às {mtime_dt.strftime('%H:%M:%S')})"
        except Exception:
            return "Erro ao ler pasta do HD"

    def check_log_for_duplicate_error(self, log_file_path):
        if not os.path.exists(log_file_path):
            return None
        try:
            lines = read_log_tail_lines(log_file_path)
            if lines:
                for line in lines[-5:]:
                    if "[ERRO_DUPLICADO]" in line:
                        return line.strip()
        except Exception:
            pass
        return None

    def get_live_viewers(self, go2rtc_ok):
        if not go2rtc_ok:
            return []
        viewers = []
        try:
            data = self.get_cached_streams_data()
            if data is None:
                return []
            for stream_name, stream_data in data.items():
                consumers = stream_data.get("consumers") or []
                for consumer in consumers:
                    addr = consumer.get("remote_addr", "")
                    ua = consumer.get("user_agent", "").lower()
                    
                    # Ignora conexões de gravação internas do Python e do ffmpeg
                    if "python" in ua or "lavf" in ua:
                        continue
                        
                    ip = addr.split(":")[0] if ":" in addr else addr
                    if ip == "127.0.0.1" or ip == "[::1]":
                        ip = "Este PC"
                        
                    browser = "Navegador"
                    if "chrome" in ua: browser = "Chrome"
                    elif "safari" in ua and "chrome" not in ua: browser = "Safari"
                    elif "firefox" in ua: browser = "Firefox"
                    elif "edge" in ua: browser = "Edge"
                    
                    viewers.append(f"{ip} ({browser})")
        except Exception:
            pass
        return viewers

    # ================= ATUALIZAÇÃO DA GUI =================
    def update_intelligence_ui(self, intelligence, generated_at=None):
        if self.silent or not hasattr(self, "lbl_intelligence_summary"):
            return
        status = intelligence.get("status", "attention")
        colors = {
            "stable": GREEN_COLOR,
            "attention": ORANGE_COLOR,
            "critical": RED_COLOR,
        }
        labels = {
            "stable": "ESTAVEL",
            "attention": "ATENCAO",
            "critical": "CRITICO",
        }
        color = colors.get(status, ORANGE_COLOR)
        label = labels.get(status, "ANALISANDO")
        collection_time = format_health_collection_time(generated_at)
        self.configure_badge_label(self.hdr_pill_brain, f"DIAGNÓSTICO: {label}", color)
        self.lbl_intelligence_confidence.configure(
            text=f"atualizado {collection_time} · confiança {intelligence.get('confidence_score', 0)}%",
            fg=color,
        )
        self.lbl_intelligence_summary.configure(
            text=intelligence.get("headline", "Analise indisponivel."),
            fg=color if status != "stable" else TEXT_COLOR,
        )
        if hasattr(self, "intelligence_band"):
            self.intelligence_band.configure(highlightbackground=color)
        actions = intelligence.get("priority_actions") or []
        action = actions[0] if actions else "Revisar o diagnostico detalhado."
        self.lbl_intelligence_action.configure(text=f"Acao: {action}")

    def update_ui_states(self, go2rtc_ok, gdrive_ok, live_viewers, cam_states, backup_count, backup_size):
        with self.status_lock:
            if self.silent:
                return
                
            recording_overview = summarize_recording_coverage(cam_states)
            any_recording = recording_overview["active_count"] > 0
            level_colors = {
                "ok": GREEN_COLOR,
                "warning": ORANGE_COLOR,
                "error": RED_COLOR,
            }

            # 0.5. Atualiza o cabeçalho dinâmico do topo (Top Status Header)
            # Pílula 1: Gravação
            self.configure_badge_label(
                self.hdr_pill_grav,
                recording_overview["label"],
                level_colors[recording_overview["level"]],
            )
                
            # Pílula 2: Câmeras Online
            online_count = sum(
                1 for state in cam_states.values()
                if camera_connectivity_status_from_state(state) == "online"
            )
            standby_count = sum(
                1 for state in cam_states.values()
                if camera_connectivity_status_from_state(state) == "standby"
            )
            total_cams = len(self.streams)
            if online_count == total_cams:
                self.configure_badge_label(self.hdr_pill_cams, f"  CÂMERAS: {online_count}/{total_cams} ONLINE  ", GREEN_COLOR)
            elif online_count > 0:
                self.configure_badge_label(self.hdr_pill_cams, f"  CÂMERAS: {online_count}/{total_cams} ONLINE  ", ORANGE_COLOR)
            elif standby_count == total_cams:
                self.configure_badge_label(self.hdr_pill_cams, "  CÂMERAS: EM ESPERA  ", ORANGE_COLOR)
            else:
                self.configure_badge_label(self.hdr_pill_cams, f"  CÂMERAS: {online_count}/{total_cams} ONLINE  ", RED_COLOR)
                
            # Pílula 3: Espaço no Disco
            if gdrive_ok:
                try:
                    total, used, free = shutil.disk_usage(GDRIVE_ROOT)
                    free_gb = free / (1024 ** 3)
                    self.configure_badge_label(self.hdr_pill_disk, f"  DISCO: {free_gb:.1f} GB LIVRES  ", GREEN_COLOR)
                except Exception:
                    self.configure_badge_label(self.hdr_pill_disk, "  DISCO: CONECTADO  ", GREEN_COLOR)
            else:
                self.configure_badge_label(self.hdr_pill_disk, "  DISCO: DESCONECTADO  ", RED_COLOR)
                
            # Pílula 4: Energia AC/Bateria
            if getattr(self, "on_battery", False):
                self.configure_badge_label(self.hdr_pill_power, "  ENERGIA: BATERIA/NOBREAK (ALERTA)  ", ORANGE_COLOR)
            else:
                self.configure_badge_label(self.hdr_pill_power, "  ENERGIA: REDE ELÉTRICA (OK)  ", GREEN_COLOR)

            # Sincroniza o estado do botão com a realidade se não estiver em transição
            if hasattr(self, "button_state") and self.button_state not in ("STARTING", "STOPPING"):
                if any_recording:
                    if self.button_state != "RECORDING":
                        self.set_button_state("RECORDING")
                else:
                    if self.button_state != "STOPPED":
                        self.set_button_state("STOPPED")
                
            # 1. go2rtc status
            if go2rtc_ok:
                self.configure_badge_label(self.lbl_val_go2rtc, "ATIVO", GREEN_COLOR)
                self.led_go2rtc.set_status(GREEN_COLOR, "#065F46")
            else:
                self.configure_badge_label(self.lbl_val_go2rtc, "INATIVO", RED_COLOR)
                self.led_go2rtc.set_status(RED_COLOR, "#991B1B")
                
            # 2. HD status
            if gdrive_ok:
                try:
                    total, used, free = shutil.disk_usage(GDRIVE_ROOT)
                    free_gb = free / (1024 ** 3)
                    self.configure_badge_label(self.lbl_val_gdrive, f"CONECTADO ({free_gb:.1f} GB livres)", GREEN_COLOR)
                except Exception:
                    self.configure_badge_label(self.lbl_val_gdrive, "CONECTADO", GREEN_COLOR)
                self.led_gdrive.set_status(GREEN_COLOR, "#065F46")
                
                if hasattr(self, "lbl_alerta_hd") and self.lbl_alerta_hd.winfo_ismapped():
                    self.lbl_alerta_hd.pack_forget()
            else:
                self.configure_badge_label(self.lbl_val_gdrive, "DESCONECTADO", RED_COLOR)
                self.led_gdrive.set_status(RED_COLOR, "#991B1B")
                
                if hasattr(self, "lbl_alerta_hd") and not self.lbl_alerta_hd.winfo_ismapped():
                    self.lbl_alerta_hd.pack(fill="x", padx=12, pady=4, before=self.cameras_main_frame)
                
            # 2.5. Backups pendentes status
            if backup_count == 0:
                self.configure_badge_label(self.lbl_val_backups, "NENHUM", GREEN_COLOR)
                self.led_backups.set_status(GREEN_COLOR, "#065F46")
            else:
                size_mb = backup_size / (1024 * 1024)
                self.configure_badge_label(self.lbl_val_backups, f"{backup_count} vídeo(s) ({size_mb:.1f} MB)", ORANGE_COLOR)
                self.led_backups.set_status(ORANGE_COLOR, "#78350F")
                
            # 3. (Removido: lbl_viewers e lbl_val_web_monitor não existem mais na UI)
                
            # 4. Atualiza os cards das câmeras
            for stream, state in cam_states.items():
                if stream in self.camera_cards:
                    card = self.camera_cards[stream]
                    camera_status = camera_connectivity_status_from_state(state)
                    camera_color = {
                        "online": GREEN_COLOR,
                        "connecting": ORANGE_COLOR,
                        "reconnecting": ORANGE_COLOR,
                        "standby": ORANGE_COLOR,
                    }.get(camera_status, RED_COLOR)
                    if "accent_bar" in card:
                        card["accent_bar"].configure(bg=camera_color)
                    if "lbl_title" in card:
                        card["lbl_title"].configure(fg=camera_color)
                    if "lbl_activity" in card:
                        activity_color = TEXT_MUTED if camera_status == "standby" else camera_color
                        card["lbl_activity"].configure(
                            text=format_camera_activity(state),
                            fg=activity_color,
                        )
                    widget = getattr(self, "camera_widgets", {}).get(stream)
                    if widget is not None:
                        widget.set_connectivity_status(camera_status)
                    
                    # Sinal
                    if camera_status == "online":
                        self.configure_badge_label(card["lbl_sinal"], "SINAL OK", GREEN_COLOR)
                        card["led_sinal"].set_status(GREEN_COLOR, "#065F46")
                    elif camera_status == "connecting":
                        self.configure_badge_label(card["lbl_sinal"], "CONECTANDO", ORANGE_COLOR)
                        card["led_sinal"].set_status(ORANGE_COLOR, "#78350F")
                    elif camera_status == "reconnecting":
                        self.configure_badge_label(card["lbl_sinal"], "RECONECTANDO", ORANGE_COLOR)
                        card["led_sinal"].set_status(ORANGE_COLOR, "#78350F")
                    elif camera_status == "standby":
                        self.configure_badge_label(card["lbl_sinal"], "EM ESPERA", ORANGE_COLOR)
                        card["led_sinal"].set_status(ORANGE_COLOR, "#78350F")
                    else:
                        self.configure_badge_label(card["lbl_sinal"], "OFFLINE", RED_COLOR)
                        card["led_sinal"].set_status(RED_COLOR, "#991B1B")
                        
                    # Gravação
                    recording_text, recording_level = camera_recording_display(state)
                    recording_color = level_colors[recording_level]
                    recording_border = {
                        "ok": "#065F46",
                        "warning": "#78350F",
                        "error": "#991B1B",
                    }[recording_level]
                    self.configure_badge_label(card["lbl_grav"], recording_text, recording_color)
                    card["led_grav"].set_status(recording_color, recording_border)
                        
                    # Web Stream
                    if "led_web" in card and "lbl_web" in card:
                        if camera_status == "online":
                            web_text = state["web_status"]
                            web_color = state["web_color"]
                            web_border = state["web_border"]
                        elif camera_status in {"connecting", "reconnecting"}:
                            web_text, web_color, web_border = "AGUARDANDO", ORANGE_COLOR, "#78350F"
                        elif camera_status == "standby":
                            web_text, web_color, web_border = "EM ESPERA", ORANGE_COLOR, "#78350F"
                        else:
                            web_text, web_color, web_border = "INDISPONÍVEL", RED_COLOR, "#991B1B"
                        self.configure_badge_label(card["lbl_web"], web_text, web_color)
                        card["led_web"].set_status(web_color, web_border)
                        
                    card["lbl_sync"].configure(text=state["sync"])

    # ================= SINCRONIZADOR DE BACKUP EM SEGUNDO PLANO =================
    def file_content_fingerprint(self, filepath, sample_size=256 * 1024):
        """Compara tamanho e amostras do inicio, meio e fim sem reler o video inteiro."""
        import hashlib

        file_size = os.path.getsize(filepath)
        digest = hashlib.sha256()
        digest.update(str(file_size).encode("ascii"))
        offsets = {
            0,
            max(0, (file_size // 2) - (sample_size // 2)),
            max(0, file_size - sample_size),
        }
        with open(filepath, "rb") as file_obj:
            for offset in sorted(offsets):
                file_obj.seek(offset)
                digest.update(file_obj.read(sample_size))
        return file_size, digest.hexdigest()

    def files_have_same_content(self, first_path, second_path):
        try:
            return self.file_content_fingerprint(first_path) == self.file_content_fingerprint(second_path)
        except Exception:
            return False

    def file_starts_with_file(self, full_path, prefix_path):
        try:
            prefix_size = os.path.getsize(prefix_path)
            if prefix_size > os.path.getsize(full_path):
                return False
            with open(full_path, "rb") as full_file, open(prefix_path, "rb") as prefix_file:
                remaining = prefix_size
                while remaining:
                    chunk_size = min(1024 * 1024, remaining)
                    if full_file.read(chunk_size) != prefix_file.read(chunk_size):
                        return False
                    remaining -= chunk_size
            return True
        except Exception:
            return False

    def get_nonconflicting_destination(self, src, dst):
        if not os.path.exists(dst) or self.files_have_same_content(src, dst):
            return dst

        base_name, extension = os.path.splitext(dst)
        stamp = datetime.now().strftime("%H%M%S")
        candidate = f"{base_name}_parte_{stamp}{extension}"
        counter = 1
        while os.path.exists(candidate) and not self.files_have_same_content(src, candidate):
            candidate = f"{base_name}_parte_{stamp}_{counter}{extension}"
            counter += 1
        return candidate

    def storage_path_is_writable(self, directory):
        if not directory:
            return False
        test_path = None
        try:
            os.makedirs(directory, exist_ok=True)
            test_name = f".nvr_write_test_{os.getpid()}_{threading.get_ident()}"
            test_path = os.path.join(directory, test_name)
            with open(test_path, "xb") as test_file:
                test_file.write(b"ok")
                test_file.flush()
                try:
                    os.fsync(test_file.fileno())
                except Exception:
                    pass
            return True
        except Exception:
            return False
        finally:
            if test_path:
                try:
                    os.remove(test_path)
                except Exception:
                    pass

    def get_recording_storage_status(self, destination_dir, gdrive_dir):
        if not gdrive_dir:
            backup_root = os.path.join(PROJ_DIR, "sistema", "backup_gravacoes")
            return garantir_limite_backup_local(backup_root)

        reserve_bytes = 15 * 1024 ** 3
        if not storage_path_matches_identity(
            gdrive_dir,
            CONFIG.get("storage_identity"),
        ):
            return {
                "ok": False,
                "free_bytes": 0,
                "reserve_bytes": reserve_bytes,
                "reason": "hd_unavailable",
            }

        try:
            _, _, free_bytes = shutil.disk_usage(destination_dir)
            return {
                "ok": free_bytes >= reserve_bytes,
                "free_bytes": free_bytes,
                "reserve_bytes": reserve_bytes,
                "reason": "ok" if free_bytes >= reserve_bytes else "hd_space_low",
            }
        except Exception:
            return {
                "ok": False,
                "free_bytes": 0,
                "reserve_bytes": reserve_bytes,
                "reason": "hd_unavailable",
            }

    def flush_recording_buffer_if_due(
        self,
        out_file,
        last_flush_time,
        now,
        interval_seconds=5.0,
    ):
        if now - last_flush_time < interval_seconds:
            return last_flush_time
        out_file.flush()
        return now

    def select_recording_destination(self, stream_name, index, escrever_log_cam):
        backup_root = os.path.join(PROJ_DIR, "sistema", "backup_gravacoes")
        gdrive_dir = self.get_gdrive_dir(stream_name, index)
        if (
            storage_path_matches_identity(gdrive_dir, CONFIG.get("storage_identity"))
            and self.storage_path_is_writable(gdrive_dir)
        ):
            hd_status = self.get_recording_storage_status(gdrive_dir, gdrive_dir)
            if hd_status["ok"]:
                previous = self.recording_destinations.get(stream_name)
                self.recording_destinations[stream_name] = "hd"
                if previous == "backup":
                    escrever_log_cam("HD disponivel novamente. Proximos blocos voltarao ao destino principal.")
                return gdrive_dir, gdrive_dir
            free_gb = hd_status["free_bytes"] / (1024 ** 3)
            escrever_log_cam(
                f"AVISO: HD principal com apenas {free_gb:.2f} GB livres. "
                "Tentando o backup local sem apagar gravacoes."
            )

        local_status = garantir_limite_backup_local(backup_root)
        if not local_status["ok"]:
            free_gb = local_status["free_bytes"] / (1024 ** 3)
            previous = self.recording_destinations.get(stream_name)
            self.recording_destinations[stream_name] = "paused"
            if previous != "paused":
                escrever_log_cam(
                    f"ERRO CRITICO: apenas {free_gb:.2f} GB livres no disco local. "
                    "Gravacao pausada para evitar preencher o Windows; nenhum backup foi apagado."
                )
            return None, ""

        backup_dir = os.path.join(backup_root, stream_name)
        if not self.storage_path_is_writable(backup_dir):
            self.recording_destinations[stream_name] = "paused"
            escrever_log_cam("ERRO CRITICO: HD e backup local estao indisponiveis. Gravacao pausada.")
            return None, ""

        previous = self.recording_destinations.get(stream_name)
        self.recording_destinations[stream_name] = "backup"
        if previous != "backup":
            escrever_log_cam(f"AVISO: HD indisponivel. Usando backup local: {backup_dir}")
        return backup_dir, ""

    def safe_atomic_copy(self, src, dst, temp_suffix=".syncing", throttle_seconds=0.0):
        """Copia para um temporario no destino e publica apenas quando estiver completo."""
        tmp_dst = dst + temp_suffix
        try:
            if os.path.exists(dst):
                if self.files_have_same_content(src, dst):
                    return True
                raise Exception(f"Destino ja existe com conteudo diferente: {dst}")

            resume_offset = 0
            if os.path.exists(tmp_dst):
                if self.file_starts_with_file(src, tmp_dst):
                    resume_offset = os.path.getsize(tmp_dst)
                else:
                    preserved_path = (
                        f"{tmp_dst}.preserved."
                        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.{os.getpid()}"
                    )
                    counter = 1
                    while os.path.exists(preserved_path):
                        preserved_path = f"{tmp_dst}.preserved.{os.getpid()}.{counter}"
                        counter += 1
                    os.replace(tmp_dst, preserved_path)

            with open(src, "rb") as f_src:
                f_src.seek(resume_offset)
                with open(tmp_dst, "ab" if resume_offset else "wb") as f_dst:
                    while True:
                        chunk = f_src.read(1024 * 1024)
                        if not chunk:
                            break
                        f_dst.write(chunk)
                        if throttle_seconds > 0:
                            time.sleep(throttle_seconds)
                    f_dst.flush()
                    try:
                        os.fsync(f_dst.fileno())
                    except Exception:
                        pass

            if os.path.getsize(src) != os.path.getsize(tmp_dst):
                raise Exception("Copia incompleta: tamanho do temporario diferente da origem")
            if not self.files_have_same_content(src, tmp_dst):
                raise Exception("Copia incompleta: conteudo do temporario diferente da origem")

            shutil.copystat(src, tmp_dst)
            os.replace(tmp_dst, dst)
            return True
        except Exception:
            raise

    def publish_recording_file(self, src, dst):
        """Publica no mesmo volume por rename; entre volumes, copia e valida."""
        if not os.path.isfile(src) or os.path.getsize(src) <= 0:
            raise Exception(f"Temporario de gravacao ausente ou vazio: {src}")

        destination_dir = os.path.dirname(dst)
        os.makedirs(destination_dir, exist_ok=True)
        if os.path.exists(dst):
            if not self.files_have_same_content(src, dst):
                raise Exception(f"Destino ja existe com conteudo diferente: {dst}")
            try:
                os.remove(src)
            except Exception:
                pass
            return True

        source_device = os.stat(src).st_dev
        destination_device = os.stat(destination_dir).st_dev
        if source_device == destination_device:
            os.replace(src, dst)
            return True

        self.safe_atomic_copy(
            src,
            dst,
            temp_suffix=".finalizing",
        )
        try:
            os.remove(src)
        except Exception:
            pass
        return True

    def recover_recording_temporaries(
        self,
        camera_dir,
        active_temp_path,
        escrever_log_cam,
        max_files=100,
    ):
        """Publica temporarios antigos reconhecidos e preserva qualquer nome desconhecido."""
        temp_dir = os.path.join(camera_dir, ".gravando_temp")
        if not os.path.isdir(temp_dir):
            return 0

        active_path = (
            os.path.normcase(os.path.abspath(active_temp_path))
            if active_temp_path
            else None
        )
        recovered = 0
        inspected = 0
        try:
            entries = os.scandir(temp_dir)
        except Exception:
            return 0

        with entries:
            for entry in entries:
                inspected += 1
                if inspected > max_files:
                    break
                if not entry.is_file(follow_symlinks=False):
                    continue
                source_path = entry.path
                if active_path and os.path.normcase(os.path.abspath(source_path)) == active_path:
                    continue
                match = re.fullmatch(
                    r"(camera_(\d{4}-\d{2}-\d{2})_\d{2}-\d{2}_ate_\d{2}-\d{2}\.ts)\.recording",
                    entry.name,
                )
                if not match:
                    continue
                try:
                    datetime.strptime(match.group(2), "%Y-%m-%d")
                    if entry.stat(follow_symlinks=False).st_size <= 0:
                        os.remove(source_path)
                        continue
                    destination_dir = os.path.join(camera_dir, match.group(2))
                    destination_path = os.path.join(destination_dir, match.group(1))
                    destination_path = self.get_nonconflicting_destination(
                        source_path,
                        destination_path,
                    )
                    self.publish_recording_file(source_path, destination_path)
                    recovered += 1
                    escrever_log_cam(
                        "Temporario preservado recuperado: "
                        f"{os.path.join(match.group(2), os.path.basename(destination_path))}"
                    )
                except Exception as error:
                    escrever_log_cam(
                        f"AVISO: temporario preservado para nova recuperacao ({str(error)})"
                    )
        return recovered

    def safe_rate_limited_copy(self, src, dst):
        return self.safe_atomic_copy(
            src,
            dst,
            temp_suffix=".syncing",
            throttle_seconds=0.1,
        )

    def background_sync_loop(self):
        while self.running_sync:
            time.sleep(30)
            
            if not storage_path_matches_identity(GDRIVE_ROOT, CONFIG.get("storage_identity")):
                continue
                
            backup_dir = os.path.join(PROJ_DIR, "sistema", "backup_gravacoes")
            if not os.path.exists(backup_dir):
                continue
                
            try:
                for idx, stream in enumerate(self.streams):
                    stream_backup_dir = os.path.join(backup_dir, stream)
                    if not os.path.exists(stream_backup_dir):
                        continue
                        
                    gdrive_dest = self.get_gdrive_dir(stream, idx)
                    
                    # Varre a pasta de backup recursivamente para achar vídeos organizados em subpastas de data
                    for root_dir, _, files in os.walk(stream_backup_dir):
                        mp4_files = [f for f in files if f.endswith((".mp4", ".ts"))]
                        if not mp4_files:
                            continue
                            
                        for filename in mp4_files:
                            local_filepath = os.path.join(root_dir, filename)
                            
                            # Organização por dia no Drive: extrai a data do nome do arquivo
                            data_dia = self.extrair_data_do_arquivo(filename)
                            if data_dia:
                                dest_folder = os.path.join(gdrive_dest, data_dia)
                            else:
                                dest_folder = gdrive_dest
                                
                            os.makedirs(dest_folder, exist_ok=True)
                            
                            # Testa permissão de escrita no Drive
                            teste_path = os.path.join(dest_folder, ".sync_test")
                            try:
                                with open(teste_path, "w") as tf:
                                    tf.write("test")
                                os.remove(teste_path)
                            except Exception:
                                continue
                                
                            dest_filepath = os.path.join(dest_folder, filename)
                            
                            mtime = os.path.getmtime(local_filepath)
                            if time.time() - mtime < 60:
                                continue
                                
                            if not self.silent:
                                self.root.after(0, lambda fn=filename, s=stream: self.add_log(f"Copiando backup de {s.upper()} para o HD: {fn}..."))
                            
                            try:
                                dest_filepath = self.get_nonconflicting_destination(local_filepath, dest_filepath)
                                self.safe_rate_limited_copy(local_filepath, dest_filepath)
                                if self.files_have_same_content(local_filepath, dest_filepath):
                                    os.remove(local_filepath)
                                    if not self.silent:
                                        self.root.after(0, lambda fn=filename, s=stream: self.add_log(f"Backup sincronizado no HD e apagado local: {fn}"))
                                else:
                                    raise Exception("validacao de conteudo falhou apos a sincronizacao")
                            except Exception as e:
                                if not self.silent:
                                    self.root.after(0, lambda fn=filename, err=str(e): self.add_log(f"Erro ao enviar {fn} para o HD: {err}"))
                                    
                # Walk backup_dir bottom-up and remove empty directories
                for root_dir, dirs, files in os.walk(backup_dir, topdown=False):
                    for d in dirs:
                        dir_path = os.path.join(root_dir, d)
                        try:
                            os.rmdir(dir_path)
                        except Exception:
                            pass
            except Exception as e:
                if not self.silent:
                    self.root.after(0, lambda err=str(e): self.add_log(f"Erro no loop de sincronizacao: {err}"))

    # ================= SISTEMA NVR INTEGRADO (GRAVAÇÃO INTERNA EM THREADS) =================
    def record_stream_thread(self, stream_name, index):
        gdrive_dir = ""
        heartbeat_dirs = set()
        lock_file = f"gravando_{stream_name}.lock"
        log_file = f"{stream_name}_erros.log"
        
        lock_path = os.path.join(LOGS_DIR, lock_file)
        log_path = os.path.join(LOGS_DIR, log_file)
        
        # Cria a trava local
        try:
            write_json_atomically(lock_path, {
                "version": 1,
                "pid": os.getpid(),
                "owner_token": getattr(self, "_recorder_owner_token", ""),
                "script_path": os.path.abspath(__file__),
                "stream": stream_name,
                "started_at": datetime.now().isoformat(timespec="seconds"),
            })
        except Exception:
            pass
            
        def escrever_log_cam(msg):
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            formatted = f"[{timestamp}] [{stream_name.upper()}] {msg}\n"
            try:
                # Rotaciona o arquivo de log se passar de 2MB
                if os.path.exists(log_path) and os.path.getsize(log_path) > 2 * 1024 * 1024:
                    try:
                        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                            lines = f.readlines()
                        if len(lines) > 500:
                            with open(log_path, "w", encoding="utf-8") as f:
                                f.writelines(lines[-500:])
                        else:
                            with open(log_path, "w", encoding="utf-8") as f:
                                f.truncate(0)
                    except Exception:
                        try:
                            with open(log_path, "w", encoding="utf-8") as f:
                                f.write(f"[{timestamp}] [ROTACAO] Log reiniciado devido a limite de tamanho.\n")
                        except Exception:
                            pass
            except Exception:
                pass
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(formatted)
            except Exception:
                pass
            if not self.silent:
                self.root.after(0, lambda: self.add_log(f"[{stream_name.upper()}] {msg}"))

        escrever_log_cam("=== INICIANDO TAREFA DE GRAVACAO INTERNA (REMUX THREAD) ===")
        
        # Loop principal da gravação
        while self.recording_active.get(stream_name, False):
            try:
                # Reavalia o destino a cada bloco para acompanhar queda ou retorno do HD.
                pasta_final, gdrive_dir = self.select_recording_destination(
                    stream_name,
                    index,
                    escrever_log_cam,
                )
                if not pasta_final:
                    self.recording_active[stream_name] = False
                    self.request_continuous_vision_sync()
                    break
                if gdrive_dir:
                    heartbeat_dirs.add(gdrive_dir)

                # Verifica duplicidade na rede
                conflito = self.verificar_duplicidade_rede_cam(gdrive_dir, stream_name) if gdrive_dir else None
                if conflito:
                    escrever_log_cam(f"[ERRO_DUPLICADO] O computador {conflito['hostname']} ({conflito['ip']}) ja esta gravando esta camera.")
                    break
                    
                # Executa gravação do bloco
                status = self.gravar_bloco_cam(stream_name, pasta_final, gdrive_dir, escrever_log_cam)
                
                if status == "parar" or status == "duplicado":
                    break

                if status == "espaco_critico":
                    self.recording_active[stream_name] = False
                    self.request_continuous_vision_sync()
                    escrever_log_cam("Gravacao encerrada com seguranca por falta de espaco local.")
                    break
                    
                if status == "erro" or status == "reconectar":
                    self.reconnect_failures[stream_name] = self.reconnect_failures.get(stream_name, 0) + 1
                    failures = self.reconnect_failures[stream_name]
                    delay = min(10 * (2 ** (failures - 1)), 300)
                    escrever_log_cam(f"Tentativa de reconexao fracassou. Aguardando {delay} segundos antes de tentar novamente (Falhas consecutivas: {failures})...")
                    
                    steps = int(delay * 2)
                    for _ in range(steps):
                        if not self.recording_active.get(stream_name, False):
                            break
                        time.sleep(0.5)
                else:
                    self.reconnect_failures[stream_name] = 0
                    if status == "rotacionar":
                        time.sleep(1)
                    else:
                        time.sleep(1)
            except Exception as e_thread:
                escrever_log_cam(f"[FALHA_GRAVADOR] Erro inesperado na thread principal: {str(e_thread)}")
                time.sleep(2.0)
                
        # Finalização e Limpeza
        if os.path.exists(lock_path):
            try:
                lock_data = load_json_file(lock_path)
                if (
                    lock_data.get("pid") == os.getpid()
                    and lock_data.get("owner_token") == getattr(self, "_recorder_owner_token", "")
                ):
                    os.remove(lock_path)
            except Exception:
                pass
                
        for heartbeat_dir in heartbeat_dirs:
            try:
                net_lock_path = os.path.join(heartbeat_dir, ".active_recorder.json")
                if os.path.exists(net_lock_path):
                    with open(net_lock_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if data.get("owner_token") == getattr(self, "_recorder_owner_token", ""):
                        os.remove(net_lock_path)
            except Exception:
                pass
            
        escrever_log_cam("=== TAREFA DE GRAVACAO INTERNA ENCERRADA ===")

    def verificar_duplicidade_rede_cam(self, gdrive_dir, stream_name):
        if not gdrive_dir:
            return None
        lock_path = os.path.join(gdrive_dir, ".active_recorder.json")
        
        if not os.path.exists(lock_path):
            return None
            
        try:
            current_time = time.time()
            if current_time - os.path.getmtime(lock_path) > 120:
                return None
            with open(lock_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("heartbeat nao e um objeto JSON")

            owner_token = data.get("owner_token")
            if owner_token and owner_token == getattr(self, "_recorder_owner_token", ""):
                return None
            if current_time - float(data.get("timestamp", 0)) < 90:
                return {
                    "hostname": data.get("hostname") or "desconhecido",
                    "ip": data.get("ip") or "",
                    "reason": "active_foreign_owner",
                }
        except Exception:
            try:
                if time.time() - os.path.getmtime(lock_path) > 120:
                    return None
            except Exception:
                pass
            return {
                "hostname": "desconhecido",
                "ip": "",
                "reason": "unreadable_recent_lock",
            }
        return None

    def atualizar_heartbeat_cam(self, gdrive_dir, stream_name):
        if not gdrive_dir:
            return True
        if not os.path.exists(gdrive_dir):
            try:
                os.makedirs(gdrive_dir, exist_ok=True)
            except Exception:
                return False
                
        lock_path = os.path.join(gdrive_dir, ".active_recorder.json")
        if self.verificar_duplicidade_rede_cam(gdrive_dir, stream_name):
            return False
        
        data = {
            "version": 1,
            "timestamp": time.time(),
            "hostname": socket.gethostname(),
            "ip": self.local_ip,
            "pid": os.getpid(),
            "stream": stream_name,
            "owner_token": getattr(self, "_recorder_owner_token", ""),
        }
        
        try:
            write_json_atomically(lock_path, data)
            observed = load_json_file(lock_path)
            return observed.get("owner_token") == data["owner_token"]
        except Exception:
            return False

    def obter_faixa_horario(self, dt):
        global CONFIG
        intervalo = CONFIG.get("bloco_minutos", 30)
        if intervalo not in (10, 15, 30):
            intervalo = 30
            
        minuto_inicio = (dt.minute // intervalo) * intervalo
        inicio = dt.replace(minute=minuto_inicio, second=0, microsecond=0)
        
        minuto_fim = minuto_inicio + intervalo
        if minuto_fim >= 60:
            fim = (dt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        else:
            fim = dt.replace(minute=minuto_fim, second=0, microsecond=0)
        return inicio, fim

    def wait_for_recording_retry(self, stream_name, seconds=2.0):
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            if not self.recording_active.get(stream_name, False):
                return False
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
        return self.recording_active.get(stream_name, False)

    def gravar_bloco_cam(self, stream_name, pasta_final, gdrive_dir, escrever_log_cam):
        agora = datetime.now()
        inicio_bloco, fim_bloco = self.obter_faixa_horario(agora)
        
        data_dia = inicio_bloco.strftime("%Y-%m-%d")
        hora_inicio = inicio_bloco.strftime("%H-%M")
        hora_fim = fim_bloco.strftime("%H-%M")
        
        # Cria subpasta com a data do dia dentro do destino para melhor organização visual
        pasta_dia_final = os.path.join(pasta_final, data_dia)
        nome_arquivo = os.path.join(pasta_dia_final, f"camera_{data_dia}_{hora_inicio}_ate_{hora_fim}.ts")
        
        # O temporario fica no proprio destino para evitar escrita duplicada no SSD.
        temp_dir = os.path.join(pasta_final, ".gravando_temp")
        os.makedirs(temp_dir, exist_ok=True)
        nome_temp = os.path.join(
            temp_dir,
            f"camera_{data_dia}_{hora_inicio}_ate_{hora_fim}.ts.recording",
        )
        
        escrever_log_cam(f"Iniciando gravacao temporaria do bloco: {os.path.basename(nome_arquivo)}")
        
        url = f"http://127.0.0.1:1984/api/stream.ts?src={stream_name}"
        
        if not self.atualizar_heartbeat_cam(gdrive_dir, stream_name):
            escrever_log_cam("[ERRO_DUPLICADO] Nao foi possivel adquirir a trava de gravacao no destino.")
            return "duplicado"
        self.recover_recording_temporaries(
            pasta_final,
            nome_temp,
            escrever_log_cam,
        )
        last_heartbeat_time = time.time()
        last_storage_check = 0.0
        
        status_ret = "reconectar"
        last_file_flush_time = time.time()
        retry_delay = 2.0
        
        # Abre o arquivo no modo append se ele já existir (reconexão dentro do mesmo bloco)
        mode = "ab" if os.path.exists(nome_temp) else "wb"
        
        try:
            with open(nome_temp, mode, buffering=1024 * 1024) as out_file:
                while datetime.now() < fim_bloco:
                    if not self.recording_active.get(stream_name, False):
                        status_ret = "parar"
                        break
                        
                    # Verifica duplicidade na rede
                    conflito = self.verificar_duplicidade_rede_cam(gdrive_dir, stream_name)
                    if conflito:
                        escrever_log_cam(f"[ERRO_DUPLICADO] O computador {conflito['hostname']} ({conflito['ip']}) ja esta gravando.")
                        status_ret = "duplicado"
                        break
                    
                    now_ts = time.time()
                    if now_ts - last_heartbeat_time >= 30:
                        if not self.atualizar_heartbeat_cam(gdrive_dir, stream_name):
                            escrever_log_cam("[ERRO_DUPLICADO] A trava de gravacao foi assumida por outro processo.")
                            status_ret = "duplicado"
                            break
                        last_heartbeat_time = now_ts

                    try:
                        req = urllib.request.Request(url)
                        response = urllib.request.urlopen(req, timeout=8)
                        self.active_connections[stream_name] = response
                        received_data_this_connection = False
                        
                        last_read_time = time.time()
                        
                        while datetime.now() < fim_bloco:
                            if not self.recording_active.get(stream_name, False):
                                status_ret = "parar"
                                break
                                
                            # Atualiza batimento cardíaco a cada 30 segundos
                            agora_ts = time.time()
                            if agora_ts - last_heartbeat_time >= 30:
                                if not self.atualizar_heartbeat_cam(gdrive_dir, stream_name):
                                    escrever_log_cam("[ERRO_DUPLICADO] Falha ao renovar a trava de gravacao.")
                                    status_ret = "duplicado"
                                    break
                                last_heartbeat_time = agora_ts

                            if agora_ts - last_storage_check >= 30:
                                storage_status = self.get_recording_storage_status(
                                    pasta_final,
                                    gdrive_dir,
                                )
                                last_storage_check = agora_ts
                                if not storage_status["ok"]:
                                    free_gb = storage_status["free_bytes"] / (1024 ** 3)
                                    storage_name = "HD principal" if gdrive_dir else "disco local"
                                    escrever_log_cam(
                                        f"ERRO CRITICO: {storage_name} indisponivel ou com apenas "
                                        f"{free_gb:.2f} GB livres. Finalizando o bloco atual."
                                    )
                                    status_ret = (
                                        "reconectar_storage"
                                        if gdrive_dir
                                        else "espaco_critico"
                                    )
                                    break
                                
                            # Leitura do fluxo de vídeo
                            try:
                                chunk = response.read(64 * 1024)
                                if not chunk:
                                    break
                                out_file.write(chunk)
                                received_data_this_connection = True
                                retry_delay = next_recording_retry_delay(
                                    retry_delay,
                                    received_data=True,
                                )
                                last_read_time = time.time()
                                self.stream_bytes_written[stream_name] = (
                                    self.stream_bytes_written.get(stream_name, 0) + len(chunk)
                                )
                                self.stream_last_data_at[stream_name] = last_read_time
                                last_file_flush_time = self.flush_recording_buffer_if_due(
                                    out_file,
                                    last_file_flush_time,
                                    last_read_time,
                                )
                            except (socket.timeout, TimeoutError):
                                if time.time() - last_read_time > 15:
                                    break
                                continue
                            except Exception:
                                break
                                
                        response.close()
                    except Exception:
                        if not self.wait_for_recording_retry(stream_name, retry_delay):
                            status_ret = "parar"
                            break
                        retry_delay = next_recording_retry_delay(retry_delay)
                        continue
                    finally:
                        self.active_connections.pop(stream_name, None)

                    if status_ret in (
                        "parar",
                        "duplicado",
                        "espaco_critico",
                        "reconectar_storage",
                    ):
                        break
                    if not received_data_this_connection:
                        if not self.wait_for_recording_retry(stream_name, retry_delay):
                            status_ret = "parar"
                            break
                        retry_delay = next_recording_retry_delay(retry_delay)
                        
                if datetime.now() >= fim_bloco:
                    status_ret = "rotacionar"
                
                try:
                    out_file.flush()
                    os.fsync(out_file.fileno())
                except Exception:
                    pass
        except Exception as e:
            escrever_log_cam(f"Erro no gravador local: {str(e)}")
            status_ret = "erro"
            
        # Move o arquivo temporário se concluído
        if os.path.exists(nome_temp) and status_ret in (
            "rotacionar",
            "parar",
            "espaco_critico",
            "reconectar_storage",
        ):
            if os.path.getsize(nome_temp) > 0:
                try:
                    os.makedirs(pasta_dia_final, exist_ok=True)
                    nome_arquivo = self.get_nonconflicting_destination(nome_temp, nome_arquivo)
                    self.publish_recording_file(
                        nome_temp,
                        nome_arquivo,
                    )
                except Exception as e_move:
                    escrever_log_cam(f"Erro ao mover bloco para {pasta_dia_final} ({str(e_move)}). Salvando no backup local.")
                    try:
                        backup_dia_dir = os.path.join(PROJ_DIR, "sistema", "backup_gravacoes", stream_name, data_dia)
                        os.makedirs(backup_dia_dir, exist_ok=True)
                        backup_arquivo = os.path.join(backup_dia_dir, f"camera_{data_dia}_{hora_inicio}_ate_{hora_fim}.ts")
                        backup_arquivo = self.get_nonconflicting_destination(nome_temp, backup_arquivo)
                        self.publish_recording_file(
                            nome_temp,
                            backup_arquivo,
                        )
                        escrever_log_cam(f"Bloco salvo no backup local de contingencia: {os.path.join(data_dia, os.path.basename(backup_arquivo))}")
                    except Exception as e_backup:
                        escrever_log_cam(f"ERRO CRITICO: Nao foi possivel salvar no backup local ({str(e_backup)})")
                else:
                    escrever_log_cam(f"Bloco publicado com seguranca na pasta definitiva: {os.path.join(data_dia, os.path.basename(nome_arquivo))}")
            else:
                try:
                    os.remove(nome_temp)
                except Exception:
                    pass
                    
        return status_ret

    # ================= CLIQUES DE BOTÕES =================
    def set_button_state(self, new_state):
        if self.silent:
            return
        self.button_state = new_state
        self.update_action_button()

    def update_action_button(self):
        if self.silent or not hasattr(self, "btn_action"):
            return
            
        if self.button_state == "STOPPED":
            self.btn_action.configure(
                text=" ▶️ Iniciar Todas as Gravações",
                bg="#059669",
                activebackground="#047857",
                state="normal",
                command=self.click_iniciar
            )
        elif self.button_state == "STARTING":
            self.btn_action.configure(
                text=" ⏳ Inicializando Serviços...",
                bg="#D97706",
                activebackground="#D97706",
                state="disabled",
                command=None
            )
        elif self.button_state == "RECORDING":
            self.btn_action.configure(
                text=" 🔴 Gravando... Clique para Parar",
                bg="#DC2626",
                activebackground="#B91C1C",
                state="normal",
                command=self.click_parar
            )
        elif self.button_state == "STOPPING":
            self.btn_action.configure(
                text=" ⏳ Salvando Vídeos e Parando...",
                bg="#4B5563",
                activebackground="#4B5563",
                state="disabled",
                command=None
            )

    def on_btn_action_enter(self, event):
        if self.button_state == "STOPPED":
            self.btn_action.configure(bg="#10B981")
        elif self.button_state == "RECORDING":
            self.btn_action.configure(bg="#EF4444")
        elif self.button_state == "STARTING":
            self.btn_action.configure(bg="#F59E0B")
        elif self.button_state == "STOPPING":
            self.btn_action.configure(bg="#4B5563")

    def on_btn_action_leave(self, event):
        if self.button_state == "STOPPED":
            self.btn_action.configure(bg="#059669")
        elif self.button_state == "RECORDING":
            self.btn_action.configure(bg="#DC2626")
        elif self.button_state == "STARTING":
            self.btn_action.configure(bg="#D97706")
        elif self.button_state == "STOPPING":
            self.btn_action.configure(bg="#4B5563")

    def animate_pulse(self):
        if not self.running_monitor:
            return
            
        if not self.silent and hasattr(self, "button_state") and hasattr(self, "btn_action"):
            if self.button_state == "RECORDING":
                current_text = self.btn_action.cget("text")
                if "🔴" in current_text:
                    self.btn_action.configure(text=" ⭕ GRAVANDO... CLIQUE PARA PARAR")
                else:
                    self.btn_action.configure(text=" 🔴 GRAVANDO... CLIQUE PARA PARAR")
            elif self.button_state == "STARTING":
                current_text = self.btn_action.cget("text")
                if "⏳" in current_text:
                    self.btn_action.configure(text=" ⚙️ INICIALIZANDO SERVIÇOS...")
                else:
                    self.btn_action.configure(text=" ⏳ INICIALIZANDO SERVIÇOS...")
            elif self.button_state == "STOPPING":
                current_text = self.btn_action.cget("text")
                if "⏳" in current_text:
                    self.btn_action.configure(text=" ⚙️ SALVANDO VÍDEOS E PARANDO...")
                else:
                    self.btn_action.configure(text=" ⏳ SALVANDO VÍDEOS E PARANDO...")
                    
        self.root.after(800, self.animate_pulse)

    def speak(self, text):
        if self.silent:
            return
        def run_speak():
            try:
                # Sintetizador de voz SAPI do Windows nativo rodando em segundo plano
                cmd = f"(New-Object -ComObject SAPI.SpVoice).Speak('{text}')"
                subprocess.run(
                    ["powershell", "-Command", cmd],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=30,
                )
            except Exception:
                pass
        threading.Thread(target=run_speak, daemon=True).start()

    def click_iniciar(self):
        if not self.silent:
            self.add_log("Iniciando gravação do sistema...")
            self.set_button_state("STARTING")
        threading.Thread(target=self.run_start_sequence, daemon=True).start()

    def run_start_sequence(self):
        with self.get_lifecycle_lock():
            try:
                self._run_start_sequence_locked()
            finally:
                startup_ready = getattr(self, "_startup_ready", None)
                if startup_ready is not None:
                    startup_ready.set()

    def _run_start_sequence_locked(self):
        # Encerra processos e threads anteriores
        self.run_stop_sequence()
        time.sleep(1.5)

        if getattr(self, "_shutdown_executed", False) or not self.running_monitor:
            return
        
        try:
            # 1. Liga a ponte RTSP go2rtc.exe se não estiver rodando
            self.iniciar_go2rtc()
            if not self.wait_for_go2rtc_recording_route():
                self.add_log(
                    "[STARTUP][ERROR] A rota MPEG-TS do go2rtc nao ficou pronta; "
                    "gravadores nao foram iniciados.",
                    "tag_erro",
                )
                return

            if getattr(self, "_shutdown_executed", False) or not self.running_monitor:
                return
                
            # 2. Liga gravadores dinamicamente em threads separadas (NVR integrado)
            for idx, stream in enumerate(self.streams):
                if getattr(self, "_shutdown_executed", False) or not self.running_monitor:
                    break
                if not self.silent:
                    self.add_log(f"Iniciando thread de gravacao da camera {stream.upper()}...")
                self.recording_active[stream] = True
                self.recording_started_at[stream] = time.time()
                self.stream_bytes_written[stream] = 0
                self.stream_last_data_at.pop(stream, None)
                t = threading.Thread(
                    target=self.record_stream_thread, 
                    args=(stream, idx), 
                    daemon=True
                )
                self.recording_threads[stream] = t
                t.start()

            self.request_continuous_vision_sync()

            if not self.silent:
                self.root.after(0, lambda: self.add_log("Inicialização concluída em segundo plano."))
                self.root.after(0, lambda: self.set_button_state("RECORDING"))
        except Exception as e:
            self.add_log(f"[STARTUP][ERROR] Falha ao iniciar gravacao: {str(e)}", "tag_erro")
            if not self.silent:
                self.root.after(0, lambda: self.add_log(f"ERRO ao iniciar gravação: {str(e)}"))
                self.root.after(0, lambda: messagebox.showerror("Erro ao Iniciar", f"Não foi possível iniciar o serviço:\n{str(e)}"))
                self.root.after(0, lambda: self.set_button_state("STOPPED"))

    def click_parar(self):
        if not self.silent:
            self.add_log("Parando gravação e finalizando processos...")
            self.set_button_state("STOPPING")
        threading.Thread(target=self.run_stop_sequence_verbose, daemon=True).start()

    def run_stop_sequence_verbose(self):
        self.run_stop_sequence()
        if not self.silent:
            self.root.after(0, lambda: self.add_log("Gravação finalizada com sucesso."))
            self.root.after(0, lambda: self.set_button_state("STOPPED"))

    def run_stop_sequence(self):
        with self.get_lifecycle_lock():
            self._run_stop_sequence_locked()

    def _run_stop_sequence_locked(self):
        with self._stop_lock:
            # 1. Sinaliza parada e fecha as leituras para que cada thread possa
            # concluir flush, fsync e publicacao do bloco atual.
            for stream in self.streams:
                self.recording_active[stream] = False
            self.request_continuous_vision_sync()

            for _, conn in list(self.active_connections.items()):
                try:
                    conn.close()
                except Exception:
                    pass

            lock_records = {}
            lock_paths = {}
            for stream in self.streams:
                lock_file = os.path.join(LOGS_DIR, f"gravando_{stream}.lock")
                lock_paths[stream] = lock_file
                if os.path.exists(lock_file):
                    try:
                        with open(lock_file, "r") as f:
                            content = f.read().strip()
                        if content.isdigit():
                            lock_records[stream] = {
                                "pid": int(content),
                                "legacy": True,
                            }
                        else:
                            lock_data = json.loads(content)
                            if isinstance(lock_data, dict) and isinstance(lock_data.get("pid"), int):
                                lock_records[stream] = lock_data
                    except Exception:
                        pass

            if not self.silent:
                self.root.after(0, lambda: self.add_log("Finalizando tarefas de gravacao..."))

            current_thread = threading.current_thread()
            local_threads = [
                (stream, thread_obj)
                for stream, thread_obj in list(self.recording_threads.items())
                if thread_obj is not current_thread and thread_obj.is_alive()
            ]
            join_deadline = time.time() + 120.0
            for stream, thread_obj in local_threads:
                remaining = max(0.0, join_deadline - time.time())
                if remaining <= 0:
                    break
                thread_obj.join(timeout=remaining)

            still_running = [stream for stream, thread_obj in local_threads if thread_obj.is_alive()]
            if still_running and not self.silent:
                cameras = ", ".join(stream.upper() for stream in still_running)
                self.root.after(
                    0,
                    lambda cams=cameras: self.add_log(
                        f"AVISO: tempo limite ao finalizar {cams}; temporarios foram preservados para recuperacao."
                    ),
                )

            # Processos externos nunca sao encerrados a partir de uma trava em
            # disco. Uma trava antiga pode conter um PID que o Windows ja
            # reutilizou para outro Python.
            my_pid = os.getpid()
            external_streams = {
                stream
                for stream, lock_data in lock_records.items()
                if lock_data.get("pid") != my_pid
                and self.is_pid_owned_recorder(lock_data.get("pid"), lock_data)
            }
            if external_streams and not self.silent:
                cameras = ", ".join(sorted(stream.upper() for stream in external_streams))
                self.root.after(
                    0,
                    lambda cams=cameras: self.add_log(
                        f"AVISO: gravador externo preservado ({cams}); use a instancia proprietaria para encerra-lo."
                    ),
                )

            for stream, lock_file in lock_paths.items():
                if stream in external_streams:
                    continue
                local_thread = self.recording_threads.get(stream)
                if local_thread is not None and local_thread.is_alive():
                    continue
                try:
                    if os.path.exists(lock_file):
                        os.remove(lock_file)
                except Exception:
                    pass

            # 3. A ponte so e encerrada depois de as gravacoes locais terem a
            # oportunidade de fechar e publicar seus arquivos.
            self.stop_managed_go2rtc()
            self.limpar_processos_ffmpeg_zumbis(sync=True)

    def click_abrir_pasta(self):
        if storage_path_matches_identity(GDRIVE_ROOT, CONFIG.get("storage_identity")):
            try:
                os.makedirs(GDRIVE_ROOT, exist_ok=True)
            except Exception:
                pass
                
        if storage_path_matches_identity(GDRIVE_ROOT, CONFIG.get("storage_identity")):
            self.add_log("Abrindo pasta de câmeras do HD FARMACIA...")
            os.startfile(GDRIVE_ROOT)
            self.flash_button(self.btn_open_folder, "✔️ Pasta Aberta!", "#10B981")
        else:
            self.add_log(f"ERRO: Pasta do HD {GDRIVE_ROOT} inacessível.")
            messagebox.showerror("Erro de Acesso", f"Não foi possível abrir a pasta do HD:\n{GDRIVE_ROOT}\n\nVerifique se o HD Externo está conectado.")

    def click_monitor(self):
        self.add_log("Abrindo Monitor no navegador...")
        import webbrowser
        webbrowser.open("http://127.0.0.1:1984/visualizador.html")
        if hasattr(self, "btn_monitor"):
            self.flash_button(self.btn_monitor, "🌐 Abrindo...", "#3B82F6")

    def set_wimi_panel_status(self, status, detail=None):
        if getattr(self, "_shutdown_executed", False):
            return
        tab_labels = {
            "starting": "  Análises (carregando)  ",
            "active": "  Análises  ",
            "error": "  Análises (atenção)  ",
        }
        notebook = getattr(self, "main_notebook", None)
        analytics_page = getattr(self, "analytics_page", None)
        if notebook is not None and analytics_page is not None:
            try:
                notebook.tab(analytics_page, text=tab_labels.get(status, tab_labels["error"]))
            except tk.TclError:
                pass
        placeholder = getattr(self, "lbl_analytics_placeholder", None)
        if placeholder is not None and placeholder.winfo_exists():
            messages = {
                "starting": "Preparando histórico, rede e visão local...",
                "active": "Análises locais prontas.",
                "error": f"Análises indisponíveis: {detail or 'verifique o log do sistema.'}",
            }
            placeholder.configure(
                text=messages.get(status, messages["error"]),
                fg=GREEN_COLOR if status == "active" else ORANGE_COLOR,
            )

    def on_main_tab_changed(self, _event=None):
        notebook = getattr(self, "main_notebook", None)
        analytics_page = getattr(self, "analytics_page", None)
        if notebook is None or analytics_page is None:
            return
        if notebook.select() == str(analytics_page):
            self.open_wimi_analytics()

    def start_wimi_analytics(self, open_panel=False):
        if getattr(self, "_analytics_shutdown", False) or getattr(self, "_shutdown_executed", False):
            return False
        with self._analytics_start_lock:
            self._analytics_open_when_ready = self._analytics_open_when_ready or open_panel
            if self._analytics_collector is not None:
                ready = True
            elif self._analytics_starting:
                return False
            else:
                self._analytics_starting = True
                ready = False
        if ready:
            if open_panel:
                self.open_wimi_analytics()
            return True
        self.set_wimi_panel_status("starting")
        threading.Thread(
            target=self._start_wimi_analytics_worker,
            name="wimi-runtime-start",
            daemon=True,
        ).start()
        return True

    def _start_wimi_analytics_worker(self):
        analytics_store = None
        biometric_store = None
        collector = None
        vision = None
        evidence_archive = None
        try:
            from wimi_analytics.backend import NvrHealthBridge
            from wimi_analytics.biometric_storage import BiometricStore
            from wimi_analytics.collector import AnalyticsCollector
            from wimi_analytics.evidence import AnonymizedEvidenceArchive
            from wimi_analytics.face_engine import LocalFaceService
            from wimi_analytics.network_diagnostics import (
                WindowsNetworkDiagnostics,
                load_or_create_identifier_key,
            )
            from wimi_analytics.person_engine import OpenCvPersonDetector
            from wimi_analytics.storage import AnalyticsStore
            from wimi_analytics.vision import VisionCoordinator

            runtime_dir = os.path.join(PROJ_DIR, "sistema", "analytics")
            forbidden_roots = [GDRIVE_ROOT] if GDRIVE_ROOT else []
            analytics_store = AnalyticsStore(
                os.path.join(runtime_dir, "wimi_analytics.sqlite3"),
                forbidden_roots=forbidden_roots,
            )
            biometric_store = BiometricStore(
                os.path.join(runtime_dir, "wimi_biometrics.sqlite3"),
                forbidden_roots=forbidden_roots,
            )
            face_service = LocalFaceService(biometric_store)
            person_detector = OpenCvPersonDetector()
            evidence_archive = AnonymizedEvidenceArchive(
                analytics_store,
                os.path.join(runtime_dir, "evidence"),
                retention_days=10,
                min_interval_seconds=900,
                max_total_bytes=768 * 1024 * 1024,
                store_identifiable_face_previews=True,
            )
            def run_analytics_maintenance(now=None):
                result = dict(evidence_archive.cleanup(now=now))
                try:
                    result["provisional_faces_deleted"] = int(
                        face_service.cleanup_provisional(now=now)
                    )
                except Exception as error:
                    result["provisional_faces_deleted"] = 0
                    result["provisional_faces_error"] = type(error).__name__
                return result

            network_identifier_key = None
            try:
                network_identifier_key = load_or_create_identifier_key(
                    os.path.join(runtime_dir, "network_identity.key")
                )
            except Exception as error:
                self.add_log(
                    "[WIMI][REDE] Identidade de dispositivos sera temporaria nesta "
                    f"sessao: {type(error).__name__}",
                    "tag_atencao",
                )
            vision = VisionCoordinator(
                store=analytics_store,
                face_service=face_service,
                evidence_archive=evidence_archive,
                person_detector=person_detector,
                hardware_guard=self.vision_hardware_guard,
                sample_interval_seconds=0.5,
                face_interval_seconds=0.5,
                person_interval_seconds=5.0,
                queue_size=2,
            )
            collector = AnalyticsCollector(
                NvrHealthBridge(os.path.join(LOGS_DIR, "health_status.json")),
                WindowsNetworkDiagnostics(
                    ttl_seconds=60,
                    identifier_key=network_identifier_key,
                ),
                analytics_store,
                interval_seconds=60,
                runtime_status_provider=self.wimi_runtime_status,
                daily_maintenance=run_analytics_maintenance,
            )
            with self._analytics_start_lock:
                self._analytics_starting = False
                open_panel = self._analytics_open_when_ready
                self._analytics_open_when_ready = False
                should_stop = self._analytics_shutdown or getattr(self, "_shutdown_executed", False)
                if not should_stop:
                    self._analytics_store = analytics_store
                    self._biometric_store = biometric_store
                    self._analytics_collector = collector
                    self._vision_coordinator = vision
                    self._face_service = face_service
                    self._evidence_archive = evidence_archive
                    self._analytics_runtime_error = None
                    vision.start()
                    collector.start()
            if should_stop:
                vision.stop(timeout=2)
                collector.stop(timeout=5)
                analytics_store.close()
                biometric_store.close()
                return
            self._ui_control_queue.put_nowait(("wimi_ready", open_panel))
        except Exception as error:
            if vision is not None:
                vision.stop(timeout=2)
            if collector is not None:
                collector.stop(timeout=5)
            if analytics_store is not None:
                analytics_store.close()
            if biometric_store is not None:
                biometric_store.close()
            with self._analytics_start_lock:
                self._analytics_starting = False
                self._analytics_open_when_ready = False
                self._analytics_runtime_error = str(error)[:300]
            message = str(error)
            self.add_log(f"WIMI Analytics indisponivel: {message}", "tag_atencao")
            try:
                self._ui_control_queue.put_nowait(("wimi_error", message))
            except queue.Full:
                pass

    def _on_wimi_analytics_ready(self, open_panel=False):
        if getattr(self, "_shutdown_executed", False):
            return
        self.set_wimi_panel_status("active")
        analytics_selected = (
            hasattr(self, "main_notebook")
            and self.main_notebook.select() == str(getattr(self, "analytics_page", ""))
        )
        if open_panel or analytics_selected:
            self.open_wimi_analytics()

    def open_wimi_analytics(self):
        if hasattr(self, "main_notebook") and hasattr(self, "analytics_page"):
            self.main_notebook.select(self.analytics_page)
        if getattr(self, "_analytics_collector", None) is None:
            self.start_wimi_analytics(open_panel=True)
            return
        if self._analytics_window is None:
            from wimi_analytics.desktop import AnalyticsDesktopWindow

            placeholder = getattr(self, "analytics_placeholder", None)
            if placeholder is not None and placeholder.winfo_exists():
                placeholder.pack_forget()

            self._analytics_window = AnalyticsDesktopWindow(
                self.root,
                self._analytics_collector,
                self._analytics_store,
                self._vision_coordinator,
                face_service=self._face_service,
                evidence_archive=getattr(self, "_evidence_archive", None),
                camera_widgets=self.camera_widgets,
                activate_cameras=self.activate_wimi_camera_analysis,
                parent=self.analytics_page,
            )
        self._analytics_window.show()

    def request_continuous_vision_sync(self):
        try:
            self.root.after(0, self.sync_continuous_vision_streams)
            return True
        except Exception:
            return False

    def sync_continuous_vision_streams(self):
        for stream, widget in getattr(self, "camera_widgets", {}).items():
            widget.set_continuous_analysis(
                bool(self.recording_active.get(stream, False))
            )

    def activate_wimi_camera_analysis(self):
        for widget in getattr(self, "camera_widgets", {}).values():
            if not widget.expanded:
                widget.expand()
        self.add_log("Visao local ativada nos previews abertos (maximo de 2 amostras/s por camera).")

    def submit_vision_frame(self, stream, image):
        vision = getattr(self, "_vision_coordinator", None)
        if vision is None or getattr(self, "_analytics_shutdown", False):
            return False
        try:
            return vision.submit(stream, image)
        except Exception:
            return False

    def get_vision_identity_overlay(self, stream):
        vision = getattr(self, "_vision_coordinator", None)
        if vision is None or getattr(self, "_analytics_shutdown", False):
            return None
        try:
            return vision.get_identity_overlay(stream, max_age_seconds=2.5)
        except Exception:
            return None

    def vision_hardware_guard(self):
        if getattr(self, "_shutdown_executed", False) or getattr(self, "_analytics_shutdown", False):
            return "shutdown"
        now = time.monotonic()
        if now - getattr(self, "_vision_guard_checked_at", 0.0) < 5.0:
            return self._vision_guard_reason
        reason = None
        memory_mb = self.get_process_memory_mb()
        if memory_mb is not None and memory_mb >= 750:
            reason = "process_memory_high"
        with self._health_lock:
            health = dict(self._health_snapshot or {})
        metrics = health.get("metrics") or {}
        hardware = health.get("hardware_summary") or {}
        issue_codes = {item.get("code") for item in health.get("issues") or []}
        if metrics.get("hd_available") is False:
            reason = reason or "recording_disk_unavailable"
        if int(hardware.get("kernel_144_new_in_session") or 0) > 0:
            reason = reason or "new_usbxhci_failure"
        if issue_codes.intersection({"PROCESS_MEMORY_HIGH", "MEMORY_GROWTH_SUSPECT"}):
            reason = reason or "resource_deterioration"
        self._vision_guard_checked_at = now
        self._vision_guard_reason = reason
        return reason

    def wimi_runtime_status(self):
        vision = getattr(self, "_vision_coordinator", None)
        face = getattr(self, "_face_service", None)
        person = getattr(vision, "person_detector", None) if vision is not None else None
        evidence = getattr(self, "_evidence_archive", None)
        snapshots = vision.snapshot() if vision is not None else {}
        active = sum(1 for item in snapshots.values() if item.get("state") == "active")
        calibrating = sum(1 for item in snapshots.values() if item.get("state") == "calibrating")
        if vision is None or not vision.running:
            vision_status = "warning"
            vision_detail = "Worker local indisponivel"
        elif active or calibrating:
            vision_status = "active" if getattr(person, "available", False) else "limited"
            face_status = str(getattr(face, "status", "indisponivel")).replace("_", " ")
            person_status = str(getattr(person, "status", "indisponivel")).replace("_", " ")
            vision_detail = (
                f"Visao ativa em {active} camera(s), calibrando {calibrating}; "
                f"pessoas {person_status}; rostos {face_status}"
            )
        else:
            vision_status = "limited"
            vision_detail = "Worker ativo; aguardando preview de camera"
        evidence_status = evidence.status() if evidence is not None else {}
        evidence_state = evidence_status.get("state", "not_configured")
        return {
            "analytics": {
                "status": "active",
                "detail": "Aba nativa integrada e historico local ativos",
                "mode": "native_embedded",
            },
            "vision": {"status": vision_status, "detail": vision_detail},
            "computers": {
                "status": "limited",
                "detail": (
                    "Aplicativos TCP deste PC monitorados localmente; "
                    "outros computadores exigem agente autorizado"
                ),
            },
            "history": {
                "status": "active" if evidence_state == "active" else "limited",
                "detail": (
                    "SQLite local e capturas anonimizadas com retencao de 10 dias; "
                    f"{evidence_status.get('count', 0)} captura(s)"
                ),
            },
        }

    def stop_wimi_analytics(self):
        analytics_lock = getattr(self, "_analytics_start_lock", None)
        if analytics_lock is None:
            self._analytics_shutdown = True
        else:
            with analytics_lock:
                self._analytics_shutdown = True
        window = getattr(self, "_analytics_window", None)
        ui_stopped = True
        if window is not None:
            try:
                window.request_destroy()
                ui_stopped = window.wait_for_workers(timeout=3)
            except Exception:
                ui_stopped = False
        vision = getattr(self, "_vision_coordinator", None)
        collector = getattr(self, "_analytics_collector", None)
        vision_stopped = True
        if vision is not None:
            vision_stopped = vision.stop(timeout=3)
        collector_stopped = True
        if collector is not None:
            collector_stopped = collector.stop(timeout=5)
        stopped = ui_stopped and vision_stopped and collector_stopped
        if stopped:
            for store in (
                getattr(self, "_analytics_store", None),
                getattr(self, "_biometric_store", None),
            ):
                if store is not None:
                    store.close()
            self._analytics_window = None
            self._vision_coordinator = None
            self._analytics_collector = None
            self._analytics_store = None
            self._biometric_store = None
            self._face_service = None
            self._evidence_archive = None
        if not stopped:
            self.add_log("WIMI Analytics ainda encerrando tarefas limitadas.", "tag_atencao")
        return stopped

    def wait_for_wimi_shutdown(self, attempts=3, retry_delay=1.0):
        attempts = max(1, min(int(attempts), 5))
        for attempt in range(attempts):
            if self.stop_wimi_analytics():
                return True
            if attempt + 1 < attempts:
                time.sleep(max(0.0, float(retry_delay)))
        return False

    def click_configurar_inicializacao(self):
        try:
            startup_folder = os.path.join(os.getenv('APPDATA'), r"Microsoft\Windows\Start Menu\Programs\Startup")
            vbs_path = os.path.join(startup_folder, "iniciar_gravacao_farmacia.vbs")
            
            # Executa apenas o gerenciador.pyw em modo silencioso/headless
            vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "{PROJ_DIR}"
WshShell.Run "pythonw.exe gerenciador.pyw --silent", 0, False
'''
            with open(vbs_path, "w", encoding="utf-8") as f:
                f.write(vbs_content)
                
            self.add_log("Inicialização automática configurada com sucesso!")
            messagebox.showinfo("Sucesso", f"O script de inicialização automática foi gerado com sucesso em:\n{vbs_path}\n\nAgora o sistema iniciará em segundo plano ao fazer logon.")
            self.flash_button(self.btn_setup_startup, "✔️ Configurado!", "#10B981")
        except Exception as e:
            self.add_log(f"ERRO ao configurar inicialização: {str(e)}")
            messagebox.showerror("Erro de Configuração", f"Não foi possível salvar o arquivo de inicialização:\n{str(e)}")

    def click_salvar_caminho(self):
        global GDRIVE_ROOT
        novo_caminho = self.entry_path.get().strip()
        if not novo_caminho:
            messagebox.showerror("Erro", "O caminho do HD não pode ser vazio.")
            return

        novo_caminho = os.path.abspath(os.path.normpath(novo_caminho))
        drive_prefix, _ = os.path.splitdrive(novo_caminho)
        drive_root = drive_prefix + os.sep if drive_prefix else ""
        if not drive_root or not os.path.exists(drive_root):
            messagebox.showerror(
                "Erro",
                "A unidade informada nao esta conectada. Conecte o HD antes de salvar o caminho.",
            )
            return

        GDRIVE_ROOT = novo_caminho
        CONFIG["gdrive_root"] = GDRIVE_ROOT
        salvar_config(CONFIG)
        
        self.add_log(f"Caminho do HD FARMACIA atualizado para: {GDRIVE_ROOT}")
        messagebox.showinfo("Caminho Salvo", f"O caminho foi atualizado com sucesso para:\n{GDRIVE_ROOT}")
        if hasattr(self, "btn_save_path"):
            self.flash_button(self.btn_save_path, "✔️ Salvo!", "#10B981")

    def click_salvar_intervalo(self):
        global CONFIG
        valor_sel = self.combo_block.get()
        try:
            minutos = int(valor_sel.split()[0])
            CONFIG["bloco_minutos"] = minutos
            salvar_config(CONFIG)
            self.add_log(f"Intervalo de gravação de vídeo atualizado para: {minutos} minutos.")
            if hasattr(self, "btn_save_interval"):
                self.flash_button(self.btn_save_interval, "✔️ Salvo!", "#10B981")
            messagebox.showinfo("Intervalo Salvo", f"O intervalo de gravação foi atualizado para {minutos} minutos com sucesso!")
        except Exception as e:
            self.add_log(f"ERRO ao salvar intervalo: {str(e)}")

    def click_escanear_corrompidos(self, show_popup=True):
        if self._scan_lock.locked():
            if not self.silent:
                self.add_log("Scanner de integridade ja esta em execucao; nova solicitacao ignorada.")
            return
        if not self.silent:
            self.add_log("Escaneamento incremental de arquivos em andamento...")
        threading.Thread(target=self.escanear_videos_corrompidos_thread, args=(show_popup,), daemon=True).start()

    def mover_video_corrompido_para_quarentena(self, filepath):
        abs_file = os.path.abspath(filepath)
        hd_root = os.path.abspath(GDRIVE_ROOT) if GDRIVE_ROOT else ""
        is_hd_file = False
        if hd_root:
            try:
                is_hd_file = os.path.commonpath([abs_file, hd_root]) == hd_root
            except Exception:
                is_hd_file = False

        if is_hd_file:
            # Mantem a quarentena no mesmo disco para evitar copiar videos
            # suspeitos para o disco do Windows.
            quarantine_root = os.path.join(GDRIVE_ROOT, ".quarentena_corrompidos")
            rel_path = os.path.relpath(abs_file, hd_root)
        else:
            quarantine_root = os.path.join(PROJ_DIR, "sistema", "quarentena_corrompidos")
            try:
                rel_path = os.path.relpath(abs_file, os.path.abspath(PROJ_DIR))
            except Exception:
                rel_path = os.path.basename(filepath)

        dest = os.path.join(quarantine_root, rel_path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        if os.path.exists(dest):
            base_name, ext = os.path.splitext(dest)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = f"{base_name}_{stamp}{ext}"

        shutil.move(filepath, dest)
        return dest

    def load_integrity_scan_state(self):
        try:
            with open(self._scan_state_path, "r", encoding="utf-8") as state_file:
                state = json.load(state_file)
            if state.get("version") == 1 and isinstance(state.get("files"), dict):
                return state
        except Exception:
            pass
        return {"version": 1, "files": {}}

    def save_integrity_scan_state(self, state):
        temp_path = self._scan_state_path + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as state_file:
                json.dump(state, state_file, ensure_ascii=True, separators=(",", ":"))
                state_file.flush()
                try:
                    os.fsync(state_file.fileno())
                except Exception:
                    pass
            os.replace(temp_path, self._scan_state_path)
        finally:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass

    def escanear_videos_corrompidos_thread(self, show_popup=True):
        if not self._scan_lock.acquire(blocking=False):
            return

        ffmpeg_bin = os.path.join(PROJ_DIR, "sistema", "go2rtc", "ffmpeg.exe")
        try:
            if not os.path.exists(ffmpeg_bin):
                if not self.silent:
                    self.add_log("ERRO: ffmpeg.exe nao encontrado para escanear.")
                return

            dirs_to_scan = []
            for idx, stream in enumerate(self.streams):
                dirs_to_scan.append((stream, os.path.join(PROJ_DIR, "sistema", "backup_gravacoes", stream)))
                gdrive_dir = self.get_gdrive_dir(stream, idx)
                if gdrive_dir and os.path.exists(gdrive_dir):
                    dirs_to_scan.append((stream, gdrive_dir))

            state = self.load_integrity_scan_state()
            file_state = state["files"]
            corrupted_count = 0
            scanned_count = 0
            skipped_count = 0
            inconclusive_count = 0
            now = time.time()
            max_files_per_run = 500 if show_popup else 200
            limit_reached = False

            for _, directory in dirs_to_scan:
                if limit_reached:
                    break
                if not os.path.exists(directory):
                    continue

                try:
                    for root_dir, _, files in os.walk(directory):
                        video_files = [f for f in files if f.lower().endswith((".mp4", ".ts"))]
                        for filename in video_files:
                            if scanned_count >= max_files_per_run or inconclusive_count >= 10:
                                limit_reached = True
                                break
                            filepath = os.path.join(root_dir, filename)
                            try:
                                stat = os.stat(filepath)
                            except Exception:
                                continue

                            # Arquivos recentes podem ainda estar sendo publicados ou sincronizados.
                            if now - stat.st_mtime < 300:
                                skipped_count += 1
                                continue

                            state_key = os.path.normcase(os.path.abspath(filepath))
                            previous = file_state.get(state_key, {})
                            same_version = (
                                previous.get("size") == stat.st_size
                                and previous.get("mtime_ns") == stat.st_mtime_ns
                            )
                            if same_version and previous.get("result") == "ok":
                                skipped_count += 1
                                continue

                            scanned_count += 1
                            scan_result = "failed"
                            try:
                                if stat.st_size == 0:
                                    scan_result = "failed"
                                else:
                                    probe_commands = [[
                                        ffmpeg_bin,
                                        "-v", "error",
                                        "-nostdin",
                                        "-i", filepath,
                                        "-t", "1",
                                        "-f", "null",
                                        "-",
                                    ]]
                                    if same_version and previous.get("failures", 0) >= 1:
                                        probe_commands.append([
                                            ffmpeg_bin,
                                            "-v", "error",
                                            "-nostdin",
                                            "-sseof", "-2",
                                            "-i", filepath,
                                            "-t", "1",
                                            "-f", "null",
                                            "-",
                                        ])

                                    return_codes = []
                                    diagnostics = []
                                    for cmd in probe_commands:
                                        completed = subprocess.run(
                                            cmd,
                                            shell=False,
                                            stdout=subprocess.DEVNULL,
                                            stderr=subprocess.PIPE,
                                            text=True,
                                            timeout=15,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                        )
                                        return_codes.append(completed.returncode)
                                        diagnostics.append(bool((completed.stderr or "").strip()))
                                    if any(code != 0 for code in return_codes):
                                        scan_result = "failed"
                                    elif any(diagnostics):
                                        scan_result = "inconclusive"
                                        inconclusive_count += 1
                                    else:
                                        scan_result = "ok"
                            except subprocess.TimeoutExpired:
                                scan_result = "inconclusive"
                                inconclusive_count += 1
                            except Exception:
                                scan_result = "inconclusive"
                                inconclusive_count += 1

                            previous_failures = previous.get("failures", 0) if same_version else 0
                            failures = previous_failures + 1 if scan_result == "failed" else 0
                            file_state[state_key] = {
                                "size": stat.st_size,
                                "mtime_ns": stat.st_mtime_ns,
                                "result": scan_result,
                                "failures": failures,
                                "checked_at": int(time.time()),
                            }
                            time.sleep(0.1)

                            # Exige duas falhas em varreduras separadas do mesmo arquivo.
                            if scan_result == "failed" and failures >= 2:
                                try:
                                    quarantine_path = self.mover_video_corrompido_para_quarentena(filepath)
                                    corrupted_count += 1
                                    file_state.pop(state_key, None)
                                    if not self.silent:
                                        self.add_log(f"[QUARENTENA] Arquivo reprovado duas vezes: {filename}")
                                    log_filepath = os.path.join(LOGS_DIR, "corrompidos_quarentena.log")
                                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    try:
                                        with open(log_filepath, "a", encoding="utf-8") as log_f:
                                            log_f.write(f"[{timestamp}] Quarentena: {filepath} -> {quarantine_path}\n")
                                    except Exception:
                                        pass
                                except Exception as quarantine_error:
                                    if not self.silent:
                                        self.add_log(f"Erro ao isolar {filename}: {str(quarantine_error)}")
                        if limit_reached:
                            break
                except Exception:
                    continue

            # Descarta entradas de arquivos que ja nao existem para limitar o estado.
            state["files"] = {
                path: item for path, item in file_state.items() if os.path.exists(path)
            }
            self.save_integrity_scan_state(state)

            if not self.silent:
                self.add_log(
                    f"Escaneamento concluido: {scanned_count} novos/alterados, "
                    f"{skipped_count} inalterados, {inconclusive_count} inconclusivos e "
                    f"{corrupted_count} em quarentena"
                    f"{' (limite seguro desta rodada atingido)' if limit_reached else ''}."
                )
                if show_popup:
                    self.root.after(
                        0,
                        lambda: messagebox.showinfo(
                            "Scanner de Integridade",
                            "Varredura concluida!\n\n"
                            f"Arquivos novos ou alterados: {scanned_count}\n"
                            f"Arquivos inalterados ignorados: {skipped_count}\n"
                            f"Resultados inconclusivos: {inconclusive_count}\n"
                            f"Arquivos isolados apos duas falhas: {corrupted_count}",
                        ),
                    )
        finally:
            self._scan_lock.release()

    def rotacionar_videos_hd(self, hd_root, max_days=None):
        retention_lock = getattr(self, "_retention_lock", None)
        if retention_lock is None:
            retention_lock = threading.Lock()
            self._retention_lock = retention_lock
        if not retention_lock.acquire(blocking=False):
            return
        try:
            if not storage_path_matches_identity(hd_root, CONFIG.get("storage_identity")):
                return
            max_days = normalize_retention_days(
                CONFIG.get("retention_days") if max_days is None else max_days
            )
            limite_data = datetime.now() - timedelta(days=max_days)
            removidos = 0
            
            # Varre apenas as pastas de gravação conhecidas (camera 1, camera 2, ...)
            for cam_path in self.get_camera_storage_dirs():
                if os.path.isdir(cam_path):
                    camera_dir = os.path.basename(cam_path)
                    # Varre as subpastas de data (YYYY-MM-DD)
                    for data_dir in os.listdir(cam_path):
                        data_path = os.path.join(cam_path, data_dir)
                        if os.path.isdir(data_path):
                            # Tenta parsear no formato YYYY-MM-DD
                            try:
                                folder_date = datetime.strptime(data_dir, "%Y-%m-%d")
                                if folder_date < limite_data:
                                    shutil.rmtree(data_path)
                                    removidos += 1
                                    if not self.silent:
                                        self.add_log(f"Pasta de gravação antiga deletada por rotação (>90 dias): {camera_dir}/{data_dir}")
                            except ValueError:
                                pass
            if removidos > 0 and not self.silent:
                self.add_log(f"Rotação de vídeos concluída: {removidos} pasta(s) de dias antigos limpa(s).")
        except Exception as e:
            if not self.silent:
                self.add_log(f"Erro na rotação de vídeos do HD: {str(e)}")

        finally:
            retention_lock.release()

    def flash_button(self, button, temp_text, temp_bg):
        old_text = button.cget("text")
        old_bg = button.cget("bg")
        button.configure(text=temp_text, bg=temp_bg)
        self.root.after(1500, lambda: button.configure(text=old_text, bg=old_bg))

    def trigger_periodic_scan(self):
        intelligence = (getattr(self, "_health_snapshot", None) or {}).get("intelligence") or {}
        protection = intelligence.get("hardware_protection") or {}
        if protection and not protection.get("heavy_maintenance_allowed", True):
            self.add_log(
                f"[INTELLIGENCE][PROTECTION] Scanner automatico adiado: {protection.get('reason')}",
                "tag_warn",
            )
            self.root.after(10800000, self.trigger_periodic_scan)
            return
        self.click_escanear_corrompidos(show_popup=False)
        self.root.after(10800000, self.trigger_periodic_scan)

    def trigger_periodic_retention(self):
        intelligence = (getattr(self, "_health_snapshot", None) or {}).get("intelligence") or {}
        protection = intelligence.get("hardware_protection") or {}
        if protection and not protection.get("heavy_maintenance_allowed", True):
            self.add_log(
                f"[INTELLIGENCE][PROTECTION] Retencao automatica adiada: {protection.get('reason')}",
                "tag_warn",
            )
        else:
            threading.Thread(
                target=self.rotacionar_videos_hd,
                args=(GDRIVE_ROOT,),
                daemon=True,
            ).start()
        self.root.after(86400000, self.trigger_periodic_retention)

    def trigger_periodic_diagnostics(self):
        """Executa diagnóstico automaticamente a cada 6 horas"""
        if not self.silent:
            self.add_log("🩺 Diagnóstico automático agendado em execução...")
        threading.Thread(target=self.run_diagnostics_sequence_auto, daemon=True).start()
        self.verificar_saude_discos_smart()
        self.root.after(21600000, self.trigger_periodic_diagnostics)

    def extrair_data_do_arquivo(self, nome_arquivo):
        import re
        match = re.search(r'(\d{4}-\d{2}-\d{2})', nome_arquivo)
        if match:
            return match.group(1)
        return None

    def adjust_window_size(self):
        pass

    def on_close_window(self):
        if not self.silent:
            self.add_log("Fechando aplicativo... Finalizando gravações de forma segura...")
            self.root.update_idletasks()
        threading.Thread(target=self.graceful_shutdown, daemon=False).start()

    def handle_exit_signal(self, signum, frame):
        self.graceful_shutdown()

    def close_tk_root(self):
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def request_tk_shutdown(self):
        if threading.current_thread() is threading.main_thread():
            self.close_tk_root()
            return
        try:
            self._ui_control_queue.put_nowait(("close_root", None))
        except queue.Full:
            pass

    def graceful_shutdown(self):
        if getattr(self, "_shutdown_executed", False):
            return
        self._shutdown_executed = True

        # Para os loops de fundo antes de matar processos; sem isso, o
        # monitor pode religar o go2rtc enquanto a janela esta fechando.
        self.running_monitor = False
        self.running_sync = False
        
        # Restaura as configurações originais de suspensão do Windows
        self.apply_prevent_sleep(False)
        
        # Para as conexões de vídeo das câmeras embutidas antes do encerramento
        if hasattr(self, "camera_widgets"):
            try:
                camera_widgets = list(self.camera_widgets.values())
                for cam_widget in camera_widgets:
                    cam_widget.stop_stream()
                    cam_widget.close_live_audio(timeout=3.0)
                    cam_widget.request_ptz_stop()
                for cam_widget in camera_widgets:
                    cam_widget.close_ptz(timeout=6.0)
            except Exception:
                pass
                
        try:
            self.run_stop_sequence()
            self.limpar_processos_ffmpeg_zumbis(sync=True)
        except Exception:
            pass

        if not self.wait_for_wimi_shutdown():
            self.add_log(
                "Encerramento adiado: uma tarefa WIMI ainda está finalizando com segurança.",
                "tag_atencao",
            )
            self._shutdown_executed = False
            return
            
        time.sleep(0.5)
        # O root tambem precisa terminar no modo --silent; caso contrario a
        # instancia antiga bloqueia a porta e impede a reinicializacao.
        self.request_tk_shutdown()

    def limpar_e_fundir_pastas_legadas(self):
        # Fusão local na nova raiz HD caso existam pastas antigas lá
        mapa_fusao = [
            ("CAMERA 1 FARMACIA", "camera 1"),
            ("CAMERA 2 FARMACIA", "camera 2"),
            ("CAMERA 3 FARMACIA_MJPEG", "camera 1"),
            ("CAMERA 4 FARMACIA2_MJPEG", "camera 2")
        ]
        
        if storage_path_matches_identity(GDRIVE_ROOT, CONFIG.get("storage_identity")):
            for pasta_origem_nome, pasta_destino_nome in mapa_fusao:
                origem_dir = os.path.join(GDRIVE_ROOT, pasta_origem_nome)
                destino_dir = os.path.join(GDRIVE_ROOT, pasta_destino_nome)
                
                if os.path.exists(origem_dir) and origem_dir != destino_dir:
                    if not self.silent:
                        self.add_log(f"Organizando pasta legada no HD: {pasta_origem_nome}...")
                    
                    try:
                        for root_dir, dirs, files in os.walk(origem_dir, topdown=False):
                            for f in files:
                                filepath_origem = os.path.join(root_dir, f)
                                rel_path = os.path.relpath(filepath_origem, origem_dir)
                                filepath_destino = os.path.join(destino_dir, rel_path)
                                
                                os.makedirs(os.path.dirname(filepath_destino), exist_ok=True)
                                try:
                                    filepath_destino = self.get_nonconflicting_destination(
                                        filepath_origem,
                                        filepath_destino,
                                    )
                                    if os.path.exists(filepath_destino):
                                        if self.files_have_same_content(filepath_origem, filepath_destino):
                                            os.remove(filepath_origem)
                                    else:
                                        # As duas pastas estao no mesmo volume; replace e atomico.
                                        os.replace(filepath_origem, filepath_destino)
                                except Exception:
                                    continue
                            
                            for d in dirs:
                                try:
                                    os.rmdir(os.path.join(root_dir, d))
                                except Exception:
                                    pass
                        try:
                            os.rmdir(origem_dir)
                        except Exception:
                            pass
                    except Exception as e:
                        if not self.silent:
                            self.add_log(f"Erro ao organizar pasta legada no HD: {str(e)}")
                            
            # 3. Deleta arquivos de lock soltos na raiz do HD
            try:
                for f in os.listdir(GDRIVE_ROOT):
                    if f.startswith(".active_recorder_") and f.endswith(".json"):
                        try:
                            os.remove(os.path.join(GDRIVE_ROOT, f))
                        except Exception:
                            pass
            except Exception:
                pass

    def auto_provision_system(self):
        self.limpar_e_fundir_pastas_legadas()
        self.recuperar_videos_orfaos()
        threading.Thread(target=self.verificar_e_aplicar_firewall, daemon=True).start()

    def recuperar_videos_orfaos(self):
        temp_root = os.path.join(PROJ_DIR, "sistema", "gravando_temp")
        if not os.path.exists(temp_root):
            return
        try:
            for stream in self.streams:
                stream_temp_dir = os.path.join(temp_root, stream)
                if not os.path.exists(stream_temp_dir):
                    continue
                files = [f for f in os.listdir(stream_temp_dir) if f.endswith((".mp4", ".ts"))]
                if not files:
                    continue
                
                dest_dir = os.path.join(PROJ_DIR, "sistema", "backup_gravacoes", stream)
                os.makedirs(dest_dir, exist_ok=True)
                
                for filename in files:
                    temp_file = os.path.join(stream_temp_dir, filename)
                    if os.path.exists(temp_file):
                        # Força o Windows a atualizar o tamanho do arquivo no NTFS
                        try:
                            with open(temp_file, "r+b") as f:
                                pass
                        except Exception:
                            pass
                            
                        try:
                            tamanho = os.path.getsize(temp_file)
                        except Exception:
                            tamanho = 0
                            
                        if tamanho > 0:
                            nome_novo = filename.replace("temp_camera_", "recuperado_camera_")
                            dest_file = os.path.join(dest_dir, nome_novo)
                            try:
                                dest_file = self.get_nonconflicting_destination(temp_file, dest_file)
                                self.safe_atomic_copy(
                                    temp_file,
                                    dest_file,
                                    temp_suffix=".recovering",
                                )
                                os.remove(temp_file)
                                self.add_log(f"Arquivo orfao recuperado com sucesso: {nome_novo}")
                            except Exception as e:
                                self.add_log(f"Erro ao mover arquivo orfao {filename}: {str(e)}")
                        else:
                            try:
                                os.remove(temp_file)
                            except Exception:
                                pass
        except Exception as e:
            self.add_log(f"Erro geral no recuperador de orfaos: {str(e)}")

    def verificar_e_aplicar_firewall(self):
        marker_file = os.path.join(LOGS_DIR, ".firewall_configured")
        if os.path.exists(marker_file):
            return
            
        try:
            res = subprocess.run(
                ["netsh", "advfirewall", "firewall", "show", "rule", "name=Camera Farmacia - API (1984)"],
                shell=False,
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if "api (1984)" in res.stdout.lower() or "câmera farmácia" in res.stdout.lower() or res.returncode == 0:
                with open(marker_file, "w") as f:
                    f.write("ok")
                return
        except Exception:
            pass
            
        if not self.silent:
            self.add_log("Configurando regras de Firewall do Windows (Solicitando permissão Admin)...")
            
        try:
            ps_cmd = (
                "New-NetFirewallRule -DisplayName 'Camera Farmacia - API (1984)' -Direction Inbound -LocalPort 1984 -Protocol TCP -Action Allow -ErrorAction SilentlyContinue; "
                "New-NetFirewallRule -DisplayName 'Camera Farmacia - RTSP (8554)' -Direction Inbound -LocalPort 8554 -Protocol TCP -Action Allow -ErrorAction SilentlyContinue; "
                "New-NetFirewallRule -DisplayName 'Camera Farmacia - WebRTC (8555)' -Direction Inbound -LocalPort 8555 -Protocol UDP -Action Allow -ErrorAction SilentlyContinue; "
                "New-NetFirewallRule -DisplayName 'Camera Farmacia - WebRTC TCP (8555)' -Direction Inbound -LocalPort 8555 -Protocol TCP -Action Allow -ErrorAction SilentlyContinue"
            )
            
            ctypes.windll.shell32.ShellExecuteW(
                None, 
                "runas", 
                "powershell.exe", 
                f"-NoProfile -WindowStyle Hidden -Command \"{ps_cmd}\"", 
                None, 
                0
            )
            
            with open(marker_file, "w") as f:
                f.write("ok")
                
            if not self.silent:
                self.add_log("Regras de Firewall configuradas com sucesso!")
        except Exception as e:
            if not self.silent:
                self.add_log(f"Falha ao configurar Firewall: {str(e)}")

    # ================= SISTEMA DE ATUALIZAÇÃO AUTOMÁTICA =================
    def is_version_newer(self, online, local):
        try:
            o_parts = [int(x) for x in online.split(".")]
            l_parts = [int(x) for x in local.split(".")]
            return o_parts > l_parts
        except Exception:
            return online != local

    def check_for_updates_thread(self):
        time.sleep(5)
        
        url_gerenciador = "https://raw.githubusercontent.com/WilliYY/camerafarmacia/main/gerenciador.pyw"
        url_visualizador = "https://raw.githubusercontent.com/WilliYY/camerafarmacia/main/sistema/visualizador.html"
        
        try:
            req = urllib.request.Request(url_gerenciador, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8) as conn:
                content = conn.read().decode('utf-8', errors='ignore')
                
            import re
            match = re.search(r'VERSION\s*=\s*["\']([^"\']+)["\']', content)
            if match:
                online_version = match.group(1)
                if self.is_version_newer(online_version, VERSION):
                    trusted_updates = normalize_trusted_update_hashes(
                        CONFIG.get("trusted_update_hashes")
                    )
                    if online_version not in trusted_updates:
                        self.add_log(
                            f"Atualizacao v{online_version} encontrada, mas sem hashes aprovados; "
                            "a instalacao automatica foi bloqueada por seguranca.",
                            "tag_warn",
                        )
                        return
                    self.add_log(f"Nova versao v{online_version} encontrada e aprovada. (Versao local: v{VERSION})")
                    self.root.after(0, lambda: self.prompt_update(online_version, url_gerenciador, url_visualizador))
            else:
                self.add_log("Nao foi possivel identificar a versao remota.")
        except Exception as e:
            self.add_log(f"Erro ao buscar atualizacoes: {str(e)}")

    def trigger_periodic_update(self):
        # Agenda nova verificação automática para daqui a 1 hora (Executado no Thread Principal)
        self.root.after(3600000, self.trigger_periodic_update)
        threading.Thread(target=self.check_for_updates_thread, daemon=True).start()
            
    def prompt_update(self, online_version, url_gerenciador, url_visualizador):
        msg = f"Uma nova versao (v{online_version}) esta disponivel no GitHub!\n\nSua versao local e v{VERSION}.\n\nDeseja atualizar o sistema automaticamente agora?"
        if messagebox.askyesno("Atualizacao Disponivel", msg):
            threading.Thread(
                target=self.run_auto_update,
                args=(online_version, url_gerenciador, url_visualizador),
                daemon=True,
            ).start()

    def download_update_payload(self, url, max_bytes):
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as conn:
            content = conn.read(max_bytes + 1)
        if not content:
            raise Exception("Atualizacao vazia recebida do servidor")
        if len(content) > max_bytes:
            raise Exception("Atualizacao excede o tamanho maximo permitido")
        return content

    def validate_update_payloads(self, expected_version, manager_content, viewer_content, trusted_hashes):
        import ast
        import hashlib
        import re

        manager_text = manager_content.decode("utf-8", errors="strict")
        ast.parse(manager_text, filename="gerenciador.pyw.tmp")

        match = re.search(r'VERSION\s*=\s*["\']([^"\']+)["\']', manager_text)
        if not match or match.group(1) != expected_version:
            raise Exception("Versao do arquivo baixado nao corresponde a atualizacao anunciada")
        for marker in (
            "class CameraManagerApp",
            "def gravar_bloco_cam",
            "def safe_atomic_copy",
            "def validate_update_payloads",
            "--wait-for-pid",
            "if __name__ == \"__main__\"",
        ):
            if marker not in manager_text:
                raise Exception(f"Arquivo principal invalido: marcador ausente ({marker})")

        viewer_text = viewer_content.decode("utf-8", errors="strict").lower()
        for marker in ("<html", "camera-grid", "loadactivestreams"):
            if marker not in viewer_text:
                raise Exception(f"Visualizador invalido: marcador ausente ({marker})")

        trusted_hashes = normalize_trusted_update_hashes({expected_version: trusted_hashes}).get(expected_version)
        if not trusted_hashes:
            raise Exception("Atualizacao sem hashes SHA-256 previamente aprovados")

        manager_hash = hashlib.sha256(manager_content).hexdigest()
        viewer_hash = hashlib.sha256(viewer_content).hexdigest()
        if manager_hash != trusted_hashes["manager_sha256"]:
            raise Exception("SHA-256 do arquivo principal nao corresponde ao valor aprovado")
        if viewer_hash != trusted_hashes["viewer_sha256"]:
            raise Exception("SHA-256 do visualizador nao corresponde ao valor aprovado")
        return manager_hash

    def write_update_stage(self, filepath, content):
        with open(filepath, "wb") as staged_file:
            staged_file.write(content)
            staged_file.flush()
            try:
                os.fsync(staged_file.fileno())
            except Exception:
                pass

    def restore_update_backup(self, backup_path, destination_path):
        if not os.path.exists(backup_path):
            return
        restore_temp = destination_path + ".rollback.tmp"
        shutil.copy2(backup_path, restore_temp)
        os.replace(restore_temp, destination_path)
            
    def run_auto_update(self, online_version, url_gerenciador, url_visualizador):
        self.add_log("Iniciando atualizacao automatica...")
        
        gerenciador_temp = os.path.join(PROJ_DIR, "gerenciador.pyw.tmp")
        visualizador_temp = os.path.join(PROJ_DIR, "sistema", "visualizador.html.tmp")
        dest_gerenciador = os.path.join(PROJ_DIR, "gerenciador.pyw")
        dest_visualizador = os.path.join(PROJ_DIR, "sistema", "visualizador.html")
        old_gerenciador = os.path.join(PROJ_DIR, "gerenciador.pyw.old")
        old_visualizador = os.path.join(PROJ_DIR, "sistema", "visualizador.html.old")
        recording_was_active = any(self.recording_active.values())
        stopped_for_update = False
        backups_ready = False
        loops_paused = False
        
        try:
            trusted_hashes = normalize_trusted_update_hashes(
                CONFIG.get("trusted_update_hashes")
            ).get(online_version)
            if not trusted_hashes:
                raise Exception("Atualizacao bloqueada: a versao nao possui hashes aprovados localmente")
            g_content = self.download_update_payload(url_gerenciador, 5 * 1024 * 1024)
            v_content = self.download_update_payload(url_visualizador, 2 * 1024 * 1024)
            update_hash = self.validate_update_payloads(
                online_version,
                g_content,
                v_content,
                trusted_hashes,
            )

            self.write_update_stage(gerenciador_temp, g_content)
            self.write_update_stage(visualizador_temp, v_content)
            self.add_log(f"Atualizacao validada antes da instalacao (SHA-256: {update_hash[:12]}...).")

            self.add_log("Parando gravacoes para aplicar atualizacao...")
            self.running_monitor = False
            self.running_sync = False
            loops_paused = True
            stopped_for_update = True
            self.run_stop_sequence()
            time.sleep(1.0)
            
            # Técnica de rename no Windows para evitar erro de arquivo travado
            shutil.copy2(dest_gerenciador, old_gerenciador)
            
            # Para o visualizador.html não precisa de rename pois ele não está travado em execução
            shutil.copy2(dest_visualizador, old_visualizador)
            backups_ready = True
            os.replace(gerenciador_temp, dest_gerenciador)
            os.replace(visualizador_temp, dest_visualizador)
            if not sync_public_viewer(PROJ_DIR):
                raise Exception("Nao foi possivel publicar o visualizador atualizado")
            
            self.add_log("Sistema atualizado com sucesso!")
            self.root.after(0, lambda: messagebox.showinfo("Atualizado", "O sistema foi atualizado com sucesso para a nova versao!\n\nO aplicativo sera reiniciado agora."))
            
            restart_args = [
                sys.executable,
                os.path.join(PROJ_DIR, "gerenciador.pyw"),
                "--wait-for-pid",
                str(os.getpid()),
            ]
            if self.silent:
                restart_args.append("--silent")
            subprocess.Popen(restart_args, creationflags=subprocess.CREATE_NO_WINDOW)
            self.root.after(0, self.graceful_shutdown)
        except Exception as e:
            self.add_log(f"ERRO durante a atualizacao: {str(e)}")
            rollback_errors = []
            if backups_ready:
                for backup_path, destination_path in (
                    (old_gerenciador, dest_gerenciador),
                    (old_visualizador, dest_visualizador),
                ):
                    try:
                        self.restore_update_backup(backup_path, destination_path)
                    except Exception as rollback_error:
                        rollback_errors.append(str(rollback_error))
                try:
                    sync_public_viewer(PROJ_DIR)
                except Exception as rollback_error:
                    rollback_errors.append(str(rollback_error))

            if rollback_errors:
                self.add_log(f"ERRO CRITICO no rollback da atualizacao: {'; '.join(rollback_errors)}")
            elif stopped_for_update and backups_ready:
                self.add_log("Arquivos anteriores restaurados apos falha na atualizacao.")
            elif stopped_for_update:
                self.add_log("Atualizacao interrompida antes da troca dos arquivos ativos.")

            for temp_file in [gerenciador_temp, visualizador_temp]:
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except Exception:
                        pass
            if loops_paused:
                self.running_monitor = True
                self.running_sync = True
                if not getattr(self, "monitor_thread", None) or not self.monitor_thread.is_alive():
                    self.monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
                    self.monitor_thread.start()
                if not getattr(self, "sync_thread", None) or not self.sync_thread.is_alive():
                    self.sync_thread = threading.Thread(target=self.background_sync_loop, daemon=True)
                    self.sync_thread.start()
            if stopped_for_update and recording_was_active and not rollback_errors:
                try:
                    self.run_start_sequence()
                except Exception as restart_error:
                    self.add_log(f"ERRO ao retomar gravacoes apos rollback: {str(restart_error)}")
            self.root.after(0, lambda: messagebox.showerror("Erro de Atualizacao", f"Nao foi possivel atualizar o sistema:\n{str(e)}"))

    # ================= DIAGNÓSTICOS =================
    def click_diagnostico(self):
        self.add_log("Gerando relatório de diagnóstico detalhado...")
        if hasattr(self, "btn_diag"):
            self.btn_diag.configure(text="⏳ Gerando...", state="disabled", bg="#374151")
        threading.Thread(target=self.run_diagnostics_sequence, daemon=True).start()

    def run_diagnostics_sequence(self):
        log = []
        log.append("==================================================")
        log.append("       RELATÓRIO DE DIAGNÓSTICO DA CÂMERA       ")
        log.append(f"       Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        log.append("==================================================")
        
        # 1. Pastas e Arquivos
        log.append("\n--- [1] VERIFICAÇÃO DE ARQUIVOS ---")
        files_to_check = {
            "Pasta do Projeto": PROJ_DIR,
            "Executável go2rtc": GO2RTC_EXE,
            "Configuração go2rtc.yaml": os.path.join(PROJ_DIR, "sistema", "go2rtc", "go2rtc.yaml"),
            "Script Gerenciador (NVR)": os.path.join(PROJ_DIR, "gerenciador.pyw")
        }
        for name, path in files_to_check.items():
            exists = os.path.exists(path)
            status = "OK" if exists else "NÃO ENCONTRADO"
            log.append(f" - {name}: {status} ({path})")

        # 2. Conectividade e DNS
        log.append("\n--- [2] CONECTIVIDADE DE REDE ---")
        try:
            host = "protect-us.ismartlife.me"
            ip = socket.gethostbyname(host)
            log.append(f" - Resolução DNS ({host}): OK (IP: {ip})")
            log.append(f" - IP Local de Rede deste PC: {self.local_ip}")
        except Exception as e:
            log.append(f" - ERRO ao resolver DNS para {host}: {str(e)}")

        # 3. Armazenamento principal
        log.append("\n--- [3] ARMAZENAMENTO PRINCIPAL ---")
        if storage_path_matches_identity(GDRIVE_ROOT, CONFIG.get("storage_identity")):
            log.append(f" - Pasta Raiz Câmeras: Encontrada ({GDRIVE_ROOT})")
            
            for idx, stream in enumerate(self.streams):
                gdrive_dir = self.get_gdrive_dir(stream, idx)
                if os.path.exists(gdrive_dir):
                    log.append(f" - Pasta Câmera {stream.upper()}: Encontrada ({gdrive_dir})")
                    test_file = os.path.join(gdrive_dir, "teste_diagnostico.tmp")
                    try:
                        with open(test_file, "w") as f:
                            f.write("teste")
                        os.remove(test_file)
                        log.append(f"   [+] Teste de Escrita {stream.upper()}: SUCESSO")
                    except Exception as e:
                        log.append(f"   [-] ERRO de escrita {stream.upper()}: {str(e)}")
                else:
                    log.append(f" - ERRO: Pasta da Câmera {stream.upper()} NÃO encontrada: {gdrive_dir}")
            
            log.append("\n [NOTA] O teste de escrita acima valida apenas a criação local dos arquivos no PC.")
            log.append("        Se o aplicativo do Google Drive exibir alertas de erro de permissão ao sincronizar,")
            log.append("        certifique-se de que a conta de e-mail vinculada possui acesso de 'Editor'")
            log.append("        (e não apenas de 'Leitor/Visualizador') nas pastas compartilhadas na nuvem.")
        else:
            log.append(f" - ERRO: destino configurado nao foi encontrado: {GDRIVE_ROOT or 'NAO CONFIGURADO'}")

        # 4. Processos em Execução
        log.append("\n--- [4] PROCESSOS EM EXECUÇÃO ---")
        go2rtc_probe = self.probe_go2rtc_api()
        log.append(f" - API go2rtc: {'RESPONDENDO' if go2rtc_probe['ok'] else 'INDISPONIVEL'}")
        for stream in self.streams:
            c_running = self.check_process_recorder(f"gravando_{stream}.lock", stream)
            log.append(f" - Gravador Câmera {stream.upper()}: {'RODANDO' if c_running else 'PARADO'}")

        # 5. Portas Locais e API go2rtc
        log.append("\n--- [5] PORTAS LOCAIS E API STREAM ---")
        s8554 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s8554.settimeout(0.5)
        try:
            s8554.connect(('127.0.0.1', 8554))
            log.append(" - Porta RTSP (8554): ABERTA (go2rtc transmitindo stream)")
            s8554.close()
        except Exception:
            log.append(" - Porta RTSP (8554): FECHADA (go2rtc inativo)")
            
        s1984 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s1984.settimeout(0.5)
        try:
            s1984.connect(('127.0.0.1', 1984))
            log.append(" - Porta API (1984): ABERTA")
            s1984.close()
            with urllib.request.urlopen("http://127.0.0.1:1984/api/streams", timeout=1.0) as conn:
                data = json.loads(conn.read().decode())
                stream_names = sorted(data.keys()) if isinstance(data, dict) else []
                log.append(f" - Streams reconhecidos pela API: {', '.join(stream_names) if stream_names else 'nenhum'}")
        except Exception as e:
            log.append(f" - Porta API (1984): FECHADA ou erro ao consultar: {str(e)}")

        # 6. Ambiente Python
        log.append("\n--- [6] AMBIENTE DO SISTEMA ---")
        log.append(f" - Versão do Python: {sys.version}")

        log.append("\n--- [7] AVALIADOR DE SAUDE ---")
        log.extend(self.get_health_report_lines())

        diag_file = os.path.join(PROJ_DIR, "sistema", "diagnostico.txt")
        try:
            with open(diag_file, "w", encoding="utf-8") as f:
                f.write("\n".join(log))
            os.startfile(diag_file)
            self.root.after(0, lambda: self.add_log("Diagnóstico gerado e aberto com sucesso!"))
        except Exception as e:
            self.root.after(0, lambda: self.add_log(f"ERRO ao salvar diagnóstico: {str(e)}"))
            self.root.after(0, lambda: messagebox.showerror("Erro Diagnóstico", f"Não foi possível salvar o arquivo:\n{str(e)}"))

    def run_diagnostics_sequence_auto(self):
        """Versão automática do diagnóstico - salva arquivo sem abrir"""
        # Reutiliza a lógica principal de coleta de dados
        log = []
        log.append("==================================================")
        log.append("       RELATÓRIO DE DIAGNÓSTICO DA CÂMERA       ")
        log.append(f"       Gerado automaticamente em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        log.append("==================================================")
        
        log.append("\n--- [1] VERIFICAÇÃO DE ARQUIVOS ---")
        files_to_check = {
            "Pasta do Projeto": PROJ_DIR,
            "Executável go2rtc": GO2RTC_EXE,
            "Configuração go2rtc.yaml": os.path.join(PROJ_DIR, "sistema", "go2rtc", "go2rtc.yaml"),
        }
        for name, path in files_to_check.items():
            exists = os.path.exists(path)
            status = "OK" if exists else "NÃO ENCONTRADO"
            log.append(f" - {name}: {status}")
        
        log.append("\n--- [2] PROCESSOS ---")
        go2rtc_probe = self.probe_go2rtc_api()
        log.append(f" - API go2rtc: {'RESPONDENDO' if go2rtc_probe['ok'] else 'INDISPONIVEL'}")
        for stream in self.streams:
            c_running = self.check_process_recorder(f"gravando_{stream}.lock", stream)
            log.append(f" - Gravador {stream.upper()}: {'RODANDO' if c_running else 'PARADO'}")
        
        log.append(f"\n--- [3] ARMAZENAMENTO PRINCIPAL ---")
        log.append(
            f" - Disponível: {'SIM' if storage_path_matches_identity(GDRIVE_ROOT, CONFIG.get('storage_identity')) else 'NÃO'}"
        )
        
        log.append(f"\n--- [4] AMBIENTE ---")
        log.append(f" - Python: {sys.version}")
        log.append(f" - IP Local: {self.local_ip}")

        log.append("\n--- [5] AVALIADOR DE SAUDE ---")
        log.extend(self.get_health_report_lines())
        
        diag_file = os.path.join(PROJ_DIR, "sistema", "diagnostico.txt")
        try:
            with open(diag_file, "w", encoding="utf-8") as f:
                f.write("\n".join(log))
            if not self.silent:
                self.root.after(0, lambda: self.add_log("Diagnóstico automático concluído com sucesso."))
        except Exception as e:
            if not self.silent:
                self.root.after(0, lambda: self.add_log(f"Erro ao salvar diagnóstico automático: {str(e)}"))

def run_standalone_health_check():
    app = CameraManagerApp.__new__(CameraManagerApp)
    app.silent = True
    configured_streams = CONFIG.get("streams") or {}
    app.streams = [name for name in configured_streams if not name.endswith(("_live", "_mjpeg"))]
    if not app.streams:
        app.streams = ["farmacia", "farmacia2"]
    app.recording_active = {stream: False for stream in app.streams}
    app.recording_threads = {}
    app.recording_destinations = {}
    app.recording_started_at = {}
    app.reconnect_failures = {}
    app._last_go2rtc_ok = False
    app.go2rtc_restart_count = 0
    app._smart_snapshot = app.query_smart_status()
    app._power_snapshot = app.read_power_snapshot()
    app._usb_report_cache = None
    app._usb_report_cache_time = 0.0

    try:
        snapshot = app.collect_health_snapshot()
        app.persist_health_snapshot(snapshot)
        print(json.dumps(snapshot, ensure_ascii=True, indent=2))
        return {"healthy": 0, "warning": 1, "critical": 2}[snapshot["overall_status"]]
    except Exception as error:
        print(json.dumps({"overall_status": "error", "error": str(error)}, ensure_ascii=True))
        return 3


_instance_socket = None

def normalize_smoke_test_seconds(value):
    seconds = int(value)
    if seconds == 0:
        return 0
    if not 30 <= seconds <= 1800:
        raise ValueError("o ensaio deve durar entre 30 e 1800 segundos")
    return seconds

def send_instance_command(command, require_ack=False, timeout_seconds=2.0):
    if command not in {"SHOW", "STOP_SAFE"}:
        raise ValueError("comando de instancia invalido")
    try:
        with socket.create_connection(("127.0.0.1", 29999), timeout=timeout_seconds) as conn:
            conn.settimeout(timeout_seconds)
            conn.sendall(command.encode("ascii"))
            if not require_ack:
                return True
            return conn.recv(16).strip() == b"OK"
    except Exception:
        return False

def wait_for_process_exit(pid, timeout_seconds=300):
    if not pid or pid <= 0 or pid == os.getpid():
        return
    try:
        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return
        try:
            ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout_seconds * 1000))
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        time.sleep(3)

def garantir_instancia_unica(silent=False):
    global _instance_socket
    try:
        _instance_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            _instance_socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            _instance_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _instance_socket.bind(('127.0.0.1', 29999))
        _instance_socket.listen(1)
        return True
    except socket.error:
        return False

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Gerenciador NVR Câmeras Farmácia")
    parser.add_argument("--silent", action="store_true", help="Inicia o sistema de gravação em segundo plano sem abrir a janela")
    parser.add_argument("--health-check", action="store_true", help="Executa diagnostico nao invasivo sem iniciar cameras")
    parser.add_argument("--smoke-test-seconds", type=int, default=0, help="Executa um ensaio real controlado de 30 a 1800 segundos")
    parser.add_argument("--safe-stop", action="store_true", help="Solicita encerramento seguro da instancia local em execucao")
    parser.add_argument("--wait-for-pid", type=int, default=0, help=argparse.SUPPRESS)
    args_cli = parser.parse_args()

    try:
        args_cli.smoke_test_seconds = normalize_smoke_test_seconds(args_cli.smoke_test_seconds)
    except ValueError as error:
        parser.error(str(error))

    if args_cli.safe_stop:
        sys.exit(0 if send_instance_command("STOP_SAFE", require_ack=True) else 1)

    if args_cli.health_check:
        sys.exit(run_standalone_health_check())

    if args_cli.wait_for_pid:
        wait_for_process_exit(args_cli.wait_for_pid)
    
    effective_silent = args_cli.silent or args_cli.smoke_test_seconds > 0

    if not garantir_instancia_unica(effective_silent):
        if not effective_silent:
            try:
                if not send_instance_command("SHOW", require_ack=True):
                    raise RuntimeError("instancia local nao respondeu")
            except Exception:
                try:
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(
                        0,
                        "O Painel de Câmeras já está em execução neste computador.\n\n"
                        "Por favor, verifique a bandeja do sistema ou as janelas abertas.",
                        "Painel de Câmeras - Já em Execução",
                        0x40 | 0x0
                    )
                except Exception:
                    pass
        sys.exit(0)
        
    # Garante que as dependências binárias existam antes de inicializar o painel
    PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
    verificar_e_baixar_dependencias(PROJ_DIR, silent=effective_silent)
    
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
        
    root = tk.Tk()
    if effective_silent:
        root.withdraw() # Esconde a janela principal!
        app = CameraManagerApp(
            root,
            silent=True,
            smoke_test_seconds=args_cli.smoke_test_seconds,
        )
    else:
        app = CameraManagerApp(root, silent=False)
        
    root.mainloop()
