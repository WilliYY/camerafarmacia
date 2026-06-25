import sys
import subprocess

# 0. Verificação e instalação automática de dependências (Pillow)
try:
    from PIL import Image, ImageTk
except ImportError:
    try:
        # Tenta instalar silenciosamente a biblioteca Pillow
        subprocess.run([sys.executable, "-m", "pip", "install", "Pillow"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        from PIL import Image, ImageTk
    except Exception as e:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            f"Não foi possível instalar a biblioteca de imagens (Pillow) automaticamente.\n\n"
            f"Erro: {str(e)}\n\n"
            f"Por favor, execute o comando 'pip install Pillow' manualmente.",
            "Erro de Dependência - NVR",
            0x10 | 0x0
        )
        sys.exit(1)

import tkinter as tk
from tkinter import ttk, messagebox
import os
import socket
import urllib.request
import json
import threading
import time
import ctypes
import shutil
from datetime import datetime, timedelta
import io
import zipfile

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
    yaml_path = os.path.join(proj_dir, "go2rtc", "go2rtc.yaml")
    ffmpeg_exe = os.path.join(proj_dir, "go2rtc", "ffmpeg.exe")
    short_ffmpeg = get_short_path(ffmpeg_exe).replace("\\", "\\\\")
    
    conteudo_padrao = f'''api:
  listen: ":1984"
  static_dir: ".."

ffmpeg:
  bin: "{short_ffmpeg}"

streams:
  # Câmeras originais (H.265 bruto - Usadas para as gravações em 0% CPU)
  farmacia: "tuya://protect-us.ismartlife.me?device_id=eb227d7fd83d2a794c4gvc&email=willian13258%40gmail.com&password=biscoito123"
  farmacia2: "tuya://protect-us.ismartlife.me?device_id=ebb17fa4c624a5e72ec6gk&email=willian13258%40gmail.com&password=biscoito123"

  # Câmeras para Visualização Web (Transcodificadas sob demanda para H.264)
  farmacia_live: "ffmpeg:farmacia#video=h264"
  farmacia2_live: "ffmpeg:farmacia2#video=h264"

  # Câmeras para Stream MJPEG (Transcodificadas sob demanda para MJPEG)
  farmacia_mjpeg: "ffmpeg:farmacia#video=mjpeg"
  farmacia2_mjpeg: "ffmpeg:farmacia2#video=mjpeg"
'''
    if not os.path.exists(yaml_path):
        os.makedirs(os.path.dirname(yaml_path), exist_ok=True)
        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write(conteudo_padrao)
        return
        
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            linhas = f.readlines()
            
        modificado = False
        ffmpeg_section = False
        for i, linha in enumerate(linhas):
            linha_strip = linha.strip()
            if linha_strip.startswith("ffmpeg:"):
                ffmpeg_section = True
                continue
            if ffmpeg_section:
                if linha_strip.startswith("bin:"):
                    indent = len(linha) - len(linha.lstrip())
                    nova_linha = " " * indent + f'bin: "{short_ffmpeg}"\n'
                    if linhas[i] != nova_linha:
                        linhas[i] = nova_linha
                        modificado = True
                    ffmpeg_section = False
                elif not linha.startswith(" ") and not linha.startswith("\t") and linha_strip != "":
                    ffmpeg_section = False
        
        if modificado:
            with open(yaml_path, "w", encoding="utf-8") as f:
                f.writelines(linhas)
    except Exception:
        try:
            with open(yaml_path, "w", encoding="utf-8") as f:
                f.write(conteudo_padrao)
        except Exception:
            pass

def verificar_e_baixar_dependencias(proj_dir, silent=False):
    go2rtc_dir = os.path.join(proj_dir, "go2rtc")
    os.makedirs(go2rtc_dir, exist_ok=True)
    
    go2rtc_exe = os.path.join(go2rtc_dir, "go2rtc.exe")
    ffmpeg_exe = os.path.join(go2rtc_dir, "ffmpeg.exe")
    
    needs_go2rtc = not os.path.exists(go2rtc_exe)
    needs_ffmpeg = not os.path.exists(ffmpeg_exe)
    
    if not needs_go2rtc and not needs_ffmpeg:
        atualizar_go2rtc_yaml(proj_dir)
        return True
        
    go2rtc_url = "https://github.com/AlexxIT/go2rtc/releases/download/v1.9.4/go2rtc_win64.zip"
    ffmpeg_url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    
    if silent:
        try:
            if needs_go2rtc:
                download_and_extract_go2rtc_silencioso(go2rtc_url, go2rtc_exe, go2rtc_dir)
            if needs_ffmpeg:
                download_and_extract_ffmpeg_silencioso(ffmpeg_url, ffmpeg_exe, go2rtc_dir)
            atualizar_go2rtc_yaml(proj_dir)
            return True
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
            import io
            try:
                # 1. Download go2rtc
                if needs_go2rtc:
                    update_gui("Baixando Ponte RTSP (go2rtc.exe)...", 0)
                    
                    req = urllib.request.Request(go2rtc_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=30) as conn:
                        total_size = int(conn.info().get('Content-Length', 0))
                        downloaded = 0
                        chunk_size = 1024 * 64
                        temp_zip = os.path.join(go2rtc_dir, "go2rtc.zip.tmp")
                        
                        with open(temp_zip, "wb") as f:
                            while True:
                                chunk = conn.read(chunk_size)
                                if not chunk:
                                    break
                                f.write(chunk)
                                downloaded += len(chunk)
                                if total_size > 0:
                                    pct = int(downloaded * 100 / total_size)
                                    val_global = int(pct * 0.3)
                                    update_gui(f"Baixando go2rtc.zip... {pct}% ({downloaded/(1024*1024):.1f}MB)", val_global)
                                    
                        update_gui("Extraindo go2rtc.exe...", 28)
                        with zipfile.ZipFile(temp_zip) as z:
                            z.extract("go2rtc.exe", go2rtc_dir)
                            
                        try:
                            os.remove(temp_zip)
                        except Exception:
                            pass
                
                # 2. Download ffmpeg
                if needs_ffmpeg:
                    update_gui("Baixando Transcodificador (ffmpeg.exe - ZIP)...", 30)
                    
                    req = urllib.request.Request(ffmpeg_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=45) as conn:
                        total_size = int(conn.info().get('Content-Length', 0))
                        downloaded = 0
                        chunk_size = 1024 * 128
                        temp_zip = os.path.join(go2rtc_dir, "ffmpeg.zip.tmp")
                        
                        with open(temp_zip, "wb") as f:
                            while True:
                                chunk = conn.read(chunk_size)
                                if not chunk:
                                    break
                                f.write(chunk)
                                downloaded += len(chunk)
                                if total_size > 0:
                                    pct = int(downloaded * 100 / total_size)
                                    val_global = 30 + int(pct * 0.6)
                                    update_gui(f"Baixando ffmpeg.zip... {pct}% ({downloaded/(1024*1024):.1f}MB)", val_global)
                        
                        update_gui("Extraindo ffmpeg.exe do arquivo ZIP...", 92)
                        with zipfile.ZipFile(temp_zip) as z:
                            ffmpeg_path_in_zip = None
                            for name in z.namelist():
                                if name.endswith("ffmpeg.exe"):
                                    ffmpeg_path_in_zip = name
                                    break
                            
                            if not ffmpeg_path_in_zip:
                                raise Exception("ffmpeg.exe não foi encontrado no arquivo ZIP.")
                                
                            with z.open(ffmpeg_path_in_zip) as source_file:
                                with open(ffmpeg_exe, "wb") as dest_file:
                                    dest_file.write(source_file.read())
                                    
                        try:
                            os.remove(temp_zip)
                        except Exception:
                            pass
                                    
                update_gui("Configurando rotas de vídeo e caminhos...", 98)
                atualizar_go2rtc_yaml(proj_dir)
                update_gui("Instalação concluída com sucesso!", 100)
                time.sleep(1.0)
                success[0] = True
                splash.after(0, splash.destroy)
                
            except Exception as e:
                error_msg[0] = str(e)
                for f in [go2rtc_exe, ffmpeg_exe, os.path.join(go2rtc_dir, "go2rtc.zip.tmp"), os.path.join(go2rtc_dir, "ffmpeg.zip.tmp")]:
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
    import zipfile
    temp_zip = os.path.join(go2rtc_dir, "go2rtc.zip.tmp")
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as conn:
        with open(temp_zip, "wb") as f:
            f.write(conn.read())
    with zipfile.ZipFile(temp_zip) as z:
        z.extract("go2rtc.exe", go2rtc_dir)
    try:
        os.remove(temp_zip)
    except Exception:
        pass

def download_and_extract_ffmpeg_silencioso(url, dest_path, go2rtc_dir):
    import zipfile
    temp_zip = os.path.join(go2rtc_dir, "ffmpeg.zip.tmp")
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=45) as conn:
        with open(temp_zip, "wb") as f:
            f.write(conn.read())
            
    with zipfile.ZipFile(temp_zip) as z:
        ffmpeg_path_in_zip = None
        for name in z.namelist():
            if name.endswith("ffmpeg.exe"):
                ffmpeg_path_in_zip = name
                break
        if ffmpeg_path_in_zip:
            with z.open(ffmpeg_path_in_zip) as source_file:
                with open(dest_path, "wb") as dest_file:
                    dest_file.write(source_file.read())
                    
    try:
        os.remove(temp_zip)
    except Exception:
        pass

# Estrutura para obter status de energia e bateria do Windows (queda de energia)
class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_byte),
        ("BatteryFlag", ctypes.c_byte),
        ("BatteryLifePercent", ctypes.c_byte),
        ("SystemStatusFlag", ctypes.c_byte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]

# Versão do Sistema (usada para o auto-update)
VERSION = "4.9"

# Configurações do Projeto
PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
GO2RTC_EXE = os.path.join(PROJ_DIR, "go2rtc", "go2rtc.exe")
LOGS_DIR = os.path.join(PROJ_DIR, "logs")

# Garante a existência das pastas do projeto
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(os.path.join(PROJ_DIR, "backup_gravacoes"), exist_ok=True)
os.makedirs(os.path.join(PROJ_DIR, "gravando_temp"), exist_ok=True)

# Arquivo de configuração local
CONFIG_PATH = os.path.join(PROJ_DIR, "config.json")

# Limpa o arquivo temporário de update da sessão anterior se existir
old_file = os.path.join(PROJ_DIR, "gerenciador.pyw.old")
if os.path.exists(old_file):
    try:
        os.remove(old_file)
    except Exception:
        pass

def detectar_gdrive_automatico():
    unidades = [f"{chr(c)}:\\" for c in range(65, 91)]  # Letras de unidade de A:\ a Z:\
    pastas_candidatas = [
        "Meu Drive\\CAMERA",
        "Meu Drive\\CAMERAS",
        "My Drive\\CAMERA",
        "My Drive\\CAMERAS",
        "CAMERA",
        "CAMERAS"
    ]
    for u in unidades:
        if os.path.exists(u):
            for p in pastas_candidatas:
                caminho = os.path.join(u, p)
                if os.path.exists(caminho):
                    # Verifica se esta pasta candidata contém a subpasta da câmera 1 para termos certeza
                    if os.path.exists(os.path.join(caminho, "CAMERA 1 FARMACIA")):
                        return caminho
    return None

def carregar_config():
    gdrive_detectado = detectar_gdrive_automatico()
    gdrive_padrao = gdrive_detectado if gdrive_detectado else r"G:\Meu Drive\CAMERAS"
    
    padrao = {"gdrive_root": gdrive_padrao}
    if not os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(padrao, f, indent=4)
        except Exception:
            pass
        return padrao
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
            if "gdrive_root" not in config:
                config["gdrive_root"] = padrao["gdrive_root"]
            # Se o caminho salvo no config.json não existir, mas conseguirmos detectar um caminho válido atual,
            # atualizamos a configuração para facilitar a portabilidade automática.
            elif not os.path.exists(config["gdrive_root"]) and gdrive_detectado:
                config["gdrive_root"] = gdrive_detectado
                salvar_config(config)
            return config
    except Exception:
        return padrao

def salvar_config(config):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
    except Exception:
        pass

CONFIG = carregar_config()
GDRIVE_ROOT = CONFIG.get("gdrive_root", r"G:\Meu Drive\CAMERAS")

# Cores do Tema Escuro Premium
BG_COLOR = "#0D0E12"       # Fundo principal cinza escuro azulado
CARD_COLOR = "#161822"     # Cards com contraste leve
ACCENT_COLOR = "#3B82F6"   # Azul moderno (Vibrant Blue)
TEXT_COLOR = "#F3F4F6"     # Texto principal claro
TEXT_MUTED = "#9CA3AF"     # Texto secundário cinza
GREEN_COLOR = "#10B981"    # Verde esmeralda (Ativo)
RED_COLOR = "#EF4444"      # Vermelho coral (Inativo)
ORANGE_COLOR = "#F59E0B"   # Laranja âmbar (Atenção)

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
        self.photo = None
        self.target_width = 620  # Tamanho padrão, será ajustado dinamicamente
        
        # Botão de cabeçalho para expandir/recolher
        self.header_btn = tk.Button(
            self,
            text=f" ▶️ CÂMERA: {stream_name.upper()}",
            font=("Segoe UI", 9, "bold"),
            fg=TEXT_COLOR,
            bg="#1F2937",
            activebackground="#374151",
            activeforeground=TEXT_COLOR,
            bd=0,
            cursor="hand2",
            padx=10,
            pady=5,
            anchor="w",
            command=self.toggle
        )
        self.header_btn.pack(fill="x", pady=2)
        
        # Frame de conteúdo que será exibido/ocultado
        self.body_frame = tk.Frame(self, bg="#020204")
        
        # Label para renderização da imagem
        self.video_lbl = tk.Label(self.body_frame, bg="#020204")
        self.video_lbl.pack(pady=4)
        
        # Frame de controles inferiores da câmera
        self.controls_frame = tk.Frame(self.body_frame, bg="#020204")
        self.controls_frame.pack(fill="x")
        
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
            padx=8,
            pady=3,
            command=self.open_fullscreen
        )
        self.btn_fullscreen.pack(side="right", padx=10, pady=2)

    def toggle(self):
        if self.expanded:
            self.collapse()
        else:
            self.expand()

    def expand(self):
        self.expanded = True
        self.header_btn.configure(text=f" ▼️ RECOLHER: {self.stream_name.upper()}", bg="#111827")
        self.body_frame.pack(fill="both", expand=True)
        self.start_stream()
        self._recalc_camera_sizes()

    def collapse(self):
        self.expanded = False
        self.header_btn.configure(text=f" ▶️ CÂMERA: {self.stream_name.upper()}", bg="#1F2937")
        self.stop_stream()
        self.body_frame.pack_forget()
        self._recalc_camera_sizes()

    def _recalc_camera_sizes(self):
        """Recalcula o tamanho das câmeras com base em quantas estão expandidas"""
        if not hasattr(self.app, 'camera_widgets'):
            return
        expanded_count = sum(1 for w in self.app.camera_widgets.values() if w.expanded)
        # Obtém a largura disponível da coluna direita
        try:
            container_width = self.master.winfo_width()
            if container_width < 100:
                container_width = 650
        except Exception:
            container_width = 650
        
        target_w = container_width - 30  # Margem lateral
        target_h = int(target_w * 9 / 16)  # Mantém 16:9
        
        # Se ambas estão abertas, reduz para caber
        if expanded_count >= 2:
            target_w = min(target_w, 560)
            target_h = int(target_w * 9 / 16)  # 560x315
        
        for w in self.app.camera_widgets.values():
            w.target_width = target_w

    def start_stream(self):
        self.running = True
        self.thread = threading.Thread(target=self.stream_loop, daemon=True)
        self.thread.start()

    def stop_stream(self):
        self.running = False
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
        """Loop principal de exibição via MJPEG stream (conexão persistente, ~15 FPS)"""
        mjpeg_url = f"http://127.0.0.1:1984/api/stream.mjpeg?src={self.stream_name}_mjpeg"
        time.sleep(0.3)
        
        while self.running:
            if not self.app.check_process_go2rtc():
                self.show_error_message("Ponte RTSP offline")
                time.sleep(1.0)
                continue
            
            try:
                last_frame_time = 0
                min_interval = 0.066  # Limita a ~15 FPS para balancear fluidez e CPU
                
                for jpeg_data in self._read_mjpeg_frames(mjpeg_url):
                    if not self.running:
                        break
                    
                    # Rate limiter para não sobrecarregar a CPU
                    now = time.time()
                    if (now - last_frame_time) < min_interval:
                        continue
                    last_frame_time = now
                    
                    try:
                        image = Image.open(io.BytesIO(jpeg_data))
                        # Redimensiona mantendo 16:9 - mostra a imagem INTEIRA sem cortes
                        tw = self.target_width
                        th = int(tw * 9 / 16)
                        image = image.resize((tw, th), Image.Resampling.BILINEAR)
                        
                        if self.running:
                            self.update_image(image)
                    except Exception:
                        pass  # Frame corrompido, pula para o próximo
                        
            except Exception as e:
                if self.running:
                    self.show_error_message(f"Reconectando...\n({type(e).__name__})")
                    time.sleep(1.0)

    def show_error_message(self, msg):
        if not self.running:
            return
        self.app.root.after(0, lambda: self.video_lbl.configure(image="", text=msg, fg=ORANGE_COLOR, font=("Segoe UI", 9, "bold"), compound="center"))

    def update_image(self, pil_image):
        photo = ImageTk.PhotoImage(pil_image)
        self.photo = photo
        self.app.root.after(0, lambda: self.video_lbl.configure(image=photo, text="", compound="none"))

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

class CameraManagerApp:
    def __init__(self, root, silent=False):
        self.root = root
        self.silent = silent
        
        # Variáveis de Controle de Threads
        self.running_monitor = True
        self.running_sync = True
        
        # Variáveis de Gravação em Memória (NVR Integrado)
        self.recording_active = {}
        self.active_connections = {}
        self.status_lock = threading.Lock()
        self.alerted_duplicates = {} # Evita exibir alerta popup repetidamente
        
        # Cache de performance para evitar chamadas repetidas
        self._cached_streams_data = None
        self._cached_streams_time = 0
        self._cached_backup_stats = (0, 0)
        self._cached_backup_time = 0
        
        self.streams = [s for s in self.parse_streams() if not s.endswith("_live") and not s.endswith("_mjpeg")]
        self.local_ip = self.get_local_ip()
        
        # Controle de energia (prevenção de suspensão e monitoramento de bateria/no-break)
        self.on_battery = False
        self.prevent_sleep_var = tk.BooleanVar(value=True)
        self.apply_prevent_sleep(True)
        
        # 1. Configura título e layout se não estiver em modo silencioso
        if not self.silent:
            self.root.title(f"Painel Câmeras Farmácia — NVR v{VERSION}")
            self.root.geometry("1280x830")
            self.root.configure(bg=BG_COLOR)
            self.root.resizable(False, False)
            
            self.setup_styles()
            self.create_widgets()
            
            # Inicializa a máquina de estados do botão e animação
            self.button_state = "STOPPED"
            self.animate_pulse()
            
            # Agenda escaneamento automático a cada 3 horas (3 * 3600 * 1000 ms)
            self.root.after(10800000, self.trigger_periodic_scan)
            
            # Agenda diagnóstico automático a cada 6 horas (6 * 3600 * 1000 ms)
            self.root.after(21600000, self.trigger_periodic_diagnostics)
            
            # Intercepta o fechamento no X do Tkinter para desligar de forma segura
            self.root.protocol("WM_DELETE_WINDOW", self.on_close_window)
            
            # Thread de verificação de atualizações no GitHub
            threading.Thread(target=self.check_for_updates_thread, daemon=True).start()
            
        # Sinais do sistema para encerramento limpo (SIGINT, SIGTERM)
        import signal
        try:
            signal.signal(signal.SIGINT, self.handle_exit_signal)
            signal.signal(signal.SIGTERM, self.handle_exit_signal)
        except Exception:
            pass

        # Auto-provisionamento de rede e recuperação de órfãos
        self.auto_provision_system()

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

    def parse_streams(self):
        yaml_path = os.path.join(PROJ_DIR, "go2rtc", "go2rtc.yaml")
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
        if index == 0:
            return os.path.join(GDRIVE_ROOT, "CAMERA 1 FARMACIA")
        elif index == 1:
            return os.path.join(GDRIVE_ROOT, "CAMERA 2 FARMACIA")
        else:
            return os.path.join(GDRIVE_ROOT, f"CAMERA {index+1} {stream_name.upper()}")

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

    def create_widgets(self):
        # Container principal dividido em duas colunas (Esquerda e Direita)
        split_container = tk.Frame(self.root, bg=BG_COLOR)
        split_container.pack(fill="both", expand=True, padx=10, pady=5)
        
        left_col = tk.Frame(split_container, bg=BG_COLOR, width=430)
        left_col.pack(side="left", fill="both", expand=False)
        left_col.pack_propagate(False)
        
        right_col = tk.Frame(split_container, bg=BG_COLOR)
        right_col.pack(side="right", fill="both", expand=True)

        # 1. HEADER / CABEÇALHO (na coluna da esquerda)
        header_frame = tk.Frame(left_col, bg=BG_COLOR, pady=8)
        header_frame.pack(fill="x", padx=12)
        
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
        
        # Divisor horizontal elegante com accent
        sep_canvas = tk.Canvas(left_col, height=2, bg=BG_COLOR, highlightthickness=0)
        sep_canvas.pack(fill="x", padx=12)
        sep_canvas.create_line(0, 1, 2000, 1, fill=ACCENT_COLOR, width=2)

        # 2. CARDS GLOBAIS (SERVIÇOS E REDE)
        top_cards_frame = tk.Frame(left_col, bg=BG_COLOR, pady=6)
        top_cards_frame.pack(fill="x", padx=12)
        
        # Card 1: Serviços Globais com borda accent superior
        card_global_wrapper = tk.Frame(top_cards_frame, bg=ACCENT_COLOR, bd=0, padx=0, pady=0)
        card_global_wrapper.pack(fill="x", expand=True, padx=4, pady=4)
        # Barra accent superior de 3px
        tk.Frame(card_global_wrapper, height=3, bg=ACCENT_COLOR).pack(fill="x")
        self.card_global = tk.Frame(card_global_wrapper, bg=CARD_COLOR, bd=0, relief="flat", padx=15, pady=10)
        self.card_global.pack(fill="x")
        tk.Label(self.card_global, text="⚡ Status dos Serviços", font=("Segoe UI", 10, "bold"), fg=TEXT_COLOR, bg=CARD_COLOR).pack(anchor="w")
        
        # Linha Ponte RTSP
        row_go2rtc = tk.Frame(self.card_global, bg=CARD_COLOR, pady=1)
        row_go2rtc.pack(anchor="w")
        self.led_go2rtc = StatusLED(row_go2rtc, size=10, bg_color=CARD_COLOR)
        self.led_go2rtc.pack(side="left", padx=(0, 6), pady=4)
        tk.Label(row_go2rtc, text="Ponte RTSP: ", font=("Segoe UI", 9), fg=TEXT_COLOR, bg=CARD_COLOR).pack(side="left")
        self.lbl_val_go2rtc = tk.Label(row_go2rtc, text="Verificando...", font=("Segoe UI", 9, "bold"), fg=ORANGE_COLOR, bg=CARD_COLOR)
        self.lbl_val_go2rtc.pack(side="left")
        
        # Linha Google Drive
        row_gdrive = tk.Frame(self.card_global, bg=CARD_COLOR, pady=1)
        row_gdrive.pack(anchor="w")
        self.led_gdrive = StatusLED(row_gdrive, size=10, bg_color=CARD_COLOR)
        self.led_gdrive.pack(side="left", padx=(0, 6), pady=4)
        tk.Label(row_gdrive, text="Google Drive G: ", font=("Segoe UI", 9), fg=TEXT_COLOR, bg=CARD_COLOR).pack(side="left")
        self.lbl_val_gdrive = tk.Label(row_gdrive, text="Verificando...", font=("Segoe UI", 9, "bold"), fg=ORANGE_COLOR, bg=CARD_COLOR)
        self.lbl_val_gdrive.pack(side="left")
        
        # Linha Backups Locais Pendentes
        row_backups = tk.Frame(self.card_global, bg=CARD_COLOR, pady=1)
        row_backups.pack(anchor="w")
        self.led_backups = StatusLED(row_backups, size=10, bg_color=CARD_COLOR)
        self.led_backups.pack(side="left", padx=(0, 6), pady=4)
        tk.Label(row_backups, text="Backups Pendentes: ", font=("Segoe UI", 9), fg=TEXT_COLOR, bg=CARD_COLOR).pack(side="left")
        self.lbl_val_backups = tk.Label(row_backups, text="Calculando...", font=("Segoe UI", 9, "bold"), fg=ORANGE_COLOR, bg=CARD_COLOR)
        self.lbl_val_backups.pack(side="left")

        # 3. GRID DINÂMICO DE CÂMERAS
        self.cameras_main_frame = tk.Frame(left_col, bg=BG_COLOR)
        self.cameras_main_frame.pack(fill="x", padx=12, pady=4)
        
        # Layout horizontal (lado a lado) para os cards de câmera
        self.camera_cards = {}
        
        for idx, stream in enumerate(self.streams):
            # Wrapper com borda accent
            card_wrapper = tk.Frame(self.cameras_main_frame, bg=ACCENT_COLOR, bd=0)
            card_wrapper.pack(side="left", fill="both", expand=True, padx=4, pady=4)
            tk.Frame(card_wrapper, height=3, bg=ACCENT_COLOR).pack(fill="x")
            card = tk.Frame(card_wrapper, bg=CARD_COLOR, bd=0, relief="flat", padx=10, pady=6)
            card.pack(fill="both", expand=True)
            
            # Título da Câmera (abreviado para caber)
            cam_label = f"CAM {idx+1}: {stream.upper()}"
            tk.Label(card, text=f"📷 {cam_label}", font=("Segoe UI", 9, "bold"), fg=ACCENT_COLOR, bg=CARD_COLOR).pack(anchor="w", pady=(0, 3))
            
            # Sinal da Câmera
            row_sinal = tk.Frame(card, bg=CARD_COLOR)
            row_sinal.pack(anchor="w", pady=0)
            led_sinal = StatusLED(row_sinal, size=8, bg_color=CARD_COLOR)
            led_sinal.pack(side="left", padx=(0, 4), pady=1)
            tk.Label(row_sinal, text="Sinal:", font=("Segoe UI", 8), fg=TEXT_COLOR, bg=CARD_COLOR).pack(side="left")
            lbl_sinal = tk.Label(row_sinal, text="...", font=("Segoe UI", 8, "bold"), fg=ORANGE_COLOR, bg=CARD_COLOR)
            lbl_sinal.pack(side="left", padx=(3, 0))
            
            # Gravação
            row_grav = tk.Frame(card, bg=CARD_COLOR)
            row_grav.pack(anchor="w", pady=0)
            led_grav = StatusLED(row_grav, size=8, bg_color=CARD_COLOR)
            led_grav.pack(side="left", padx=(0, 4), pady=1)
            tk.Label(row_grav, text="Grav:", font=("Segoe UI", 8), fg=TEXT_COLOR, bg=CARD_COLOR).pack(side="left")
            lbl_grav = tk.Label(row_grav, text="...", font=("Segoe UI", 8, "bold"), fg=ORANGE_COLOR, bg=CARD_COLOR)
            lbl_grav.pack(side="left", padx=(3, 0))

            # Web Stream
            row_web = tk.Frame(card, bg=CARD_COLOR)
            row_web.pack(anchor="w", pady=0)
            led_web = StatusLED(row_web, size=8, bg_color=CARD_COLOR)
            led_web.pack(side="left", padx=(0, 4), pady=1)
            tk.Label(row_web, text="Stream:", font=("Segoe UI", 8), fg=TEXT_COLOR, bg=CARD_COLOR).pack(side="left")
            lbl_web = tk.Label(row_web, text="...", font=("Segoe UI", 8, "bold"), fg=ORANGE_COLOR, bg=CARD_COLOR)
            lbl_web.pack(side="left", padx=(3, 0))
            
            # Última gravação/Sync
            lbl_sync = tk.Label(card, text="Buscando...", font=("Segoe UI", 7), fg=TEXT_MUTED, bg=CARD_COLOR, justify="left", wraplength=170)
            lbl_sync.pack(anchor="w", pady=(2, 0))
            
            # Salva referências para atualização
            self.camera_cards[stream] = {
                "led_sinal": led_sinal,
                "lbl_sinal": lbl_sinal,
                "led_grav": led_grav,
                "lbl_grav": lbl_grav,
                "led_web": led_web,
                "lbl_web": lbl_web,
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

        # Controle de suspensão de energia
        sleep_frame = tk.Frame(left_col, bg=BG_COLOR, pady=2)
        sleep_frame.pack(fill="x", padx=12)
        
        self.chk_prevent_sleep = tk.Checkbutton(
            sleep_frame,
            text=" 🖥️ Impedir Suspensão/Desligamento do PC",
            variable=self.prevent_sleep_var,
            font=("Segoe UI", 9, "bold"),
            fg=TEXT_COLOR,
            bg=BG_COLOR,
            activebackground=BG_COLOR,
            activeforeground=TEXT_COLOR,
            selectcolor="#111827",
            bd=0,
            cursor="hand2",
            command=self.toggle_prevent_sleep
        )
        self.chk_prevent_sleep.pack(anchor="w", padx=4, pady=2)

        # 4.5. CONFIGURAÇÕES DE CAMINHO E INTEGRIDADE
        config_frame = tk.Frame(left_col, bg=BG_COLOR, pady=2)
        config_frame.pack(fill="x", padx=12, pady=2)
        
        path_label = tk.Label(config_frame, text="Pasta Drive/Rede:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_COLOR)
        path_label.pack(side="left", padx=2)
        
        self.entry_path = tk.Entry(config_frame, bg="#161822", fg=TEXT_COLOR, font=("Segoe UI", 9), bd=1, relief="solid", width=20, insertbackground=TEXT_COLOR)
        self.entry_path.insert(0, GDRIVE_ROOT)
        self.entry_path.pack(side="left", padx=2)
        
        self.btn_save_path = tk.Button(
            config_frame,
            text="Salvar",
            font=("Segoe UI", 8, "bold"),
            fg=TEXT_COLOR,
            bg="#3B82F6",
            activebackground="#2563EB",
            activeforeground=TEXT_COLOR,
            bd=0,
            cursor="hand2",
            padx=8,
            pady=2,
            command=self.click_salvar_caminho
        )
        self.btn_save_path.pack(side="left", padx=2)

        # 5. LOG DE EVENTOS (CONSOLE PREMIUM)
        log_title_frame = tk.Frame(left_col, bg=BG_COLOR)
        log_title_frame.pack(fill="x", padx=15, pady=(4,0))
        tk.Label(log_title_frame, text="📝 Log de Eventos", font=("Segoe UI", 9, "bold"), fg=TEXT_COLOR, bg=BG_COLOR).pack(anchor="w")
        
        self.txt_log = tk.Text(left_col, height=6, bg="#030712", fg="#34D399", font=("Consolas", 9), bd=0, padx=10, pady=5, wrap="word")
        self.txt_log.pack(fill="both", expand=True, padx=12, pady=(2, 6))
        self.txt_log.configure(state="disabled")
        # Configura tags de cores para diferentes tipos de mensagens
        self.txt_log.tag_configure("tag_erro", foreground="#EF4444")
        self.txt_log.tag_configure("tag_ok", foreground="#10B981")
        self.txt_log.tag_configure("tag_info", foreground="#60A5FA")
        self.txt_log.tag_configure("tag_warn", foreground="#F59E0B")
        self.txt_log.tag_configure("tag_default", foreground="#34D399")

        # Descarrega logs gerados durante o startup/boot
        if hasattr(self, "_startup_logs"):
            logs_to_flush = self._startup_logs
            del self._startup_logs
            for msg_item in logs_to_flush:
                self.add_log(msg_item)

        # 3.5. CONTAINERS DAS CÂMERAS AO VIVO (na coluna da direita)
        self.live_cams_container = tk.Frame(right_col, bg=BG_COLOR)
        self.live_cams_container.pack(fill="both", expand=True, padx=10, pady=4)
        
        self.camera_widgets = {}
        for stream in self.streams:
            cam_widget = LiveCameraWidget(self.live_cams_container, stream, self)
            cam_widget.pack(side="top", fill="both", expand=True, pady=4)
            self.camera_widgets[stream] = cam_widget

    # ================= LOG DE EVENTOS =================
    def add_log(self, msg):
        # Se o console de log (txt_log) ainda não existe ou se estamos rodando ocultos (silent)
        if not hasattr(self, "txt_log") or self.txt_log is None or self.silent:
            if not hasattr(self, "_startup_logs"):
                self._startup_logs = []
            self._startup_logs.append(msg)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
            return

        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted = f"[{timestamp}] {msg}\n"
        
        # Determina a tag de cor baseada no conteúdo
        msg_lower = msg.lower()
        if "erro" in msg_lower or "falha" in msg_lower or "crítico" in msg_lower or "excluído" in msg_lower:
            tag = "tag_erro"
        elif "sucesso" in msg_lower or "concluíd" in msg_lower or "ativo" in msg_lower or "ok" in msg_lower or "configurad" in msg_lower:
            tag = "tag_ok"
        elif "iniciando" in msg_lower or "escaneamento" in msg_lower or "diagnóstico" in msg_lower or "verificando" in msg_lower or "automátic" in msg_lower:
            tag = "tag_info"
        elif "aviso" in msg_lower or "aguardando" in msg_lower or "tentando" in msg_lower or "parando" in msg_lower:
            tag = "tag_warn"
        else:
            tag = "tag_default"
        
        try:
            self.txt_log.configure(state="normal")
            self.txt_log.insert(tk.END, formatted, tag)
            
            # Auto-cleanup: limita o log a 200 linhas para evitar consumo de memória
            line_count = int(self.txt_log.index('end-1c').split('.')[0])
            if line_count > 200:
                self.txt_log.delete('1.0', f'{line_count - 200}.0')
            
            self.txt_log.see(tk.END)
            self.txt_log.configure(state="disabled")
        except Exception:
            pass

    def copy_link_to_clipboard(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(f"http://{self.local_ip}:1984")
        self.add_log("Link Web copiado para a área de transferência!")
        messagebox.showinfo("Copiado", f"O link http://{self.local_ip}:1984 foi copiado com sucesso!")

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
        while self.running_monitor:
            # 0. Verifica quedas de energia / status da bateria do PC/Nobreak
            self.check_power_status()
            
            # 1. Verifica se go2rtc está ativo
            go2rtc_ok = self.check_process_go2rtc()
            
            # 2. Verifica se o Google Drive está conectado
            gdrive_ok = os.path.exists(GDRIVE_ROOT)
            
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
                    "sync": last_file_str,
                    "duplicate_error": duplicate_msg is not None,
                    "web_status": web_status,
                    "web_color": web_color,
                    "web_border": web_border
                }
            
            # Atualiza a interface (se não estiver em modo silencioso)
            if not self.silent:
                self.root.after(0, self.update_ui_states, go2rtc_ok, gdrive_ok, live_viewers, cam_states, backup_count, backup_size)
            
            # Dorme por 3 segundos
            time.sleep(3)

    def get_backup_stats(self):
        """Retorna contagem e tamanho dos backups locais pendentes com cache de 30 segundos"""
        now = time.time()
        if (now - self._cached_backup_time) < 30.0:
            return self._cached_backup_stats
        
        backup_dir = os.path.join(PROJ_DIR, "backup_gravacoes")
        if not os.path.exists(backup_dir):
            self._cached_backup_stats = (0, 0)
            self._cached_backup_time = now
            return 0, 0
        total_files = 0
        total_size = 0
        try:
            for root_dir, _, files in os.walk(backup_dir):
                for f in files:
                    if f.endswith(".mp4"):
                        total_files += 1
                        total_size += os.path.getsize(os.path.join(root_dir, f))
        except Exception:
            pass
        self._cached_backup_stats = (total_files, total_size)
        self._cached_backup_time = now
        return total_files, total_size

    def toggle_prevent_sleep(self):
        if self.prevent_sleep_var.get():
            self.apply_prevent_sleep(True)
        else:
            self.apply_prevent_sleep(False)

    def apply_prevent_sleep(self, enable):
        try:
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ES_DISPLAY_REQUIRED = 0x00000002
            
            if enable:
                # Informa ao Windows para manter o sistema ativo
                ctypes.windll.kernel32.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
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

    def check_power_status(self):
        try:
            import ctypes
            status = SYSTEM_POWER_STATUS()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
                # ACLineStatus: 0 = Offline (rodando em bateria), 1 = Online (energia AC), 255 = Desconhecido
                ac_status = status.ACLineStatus
                battery_percent = status.BatteryLifePercent
                
                if ac_status == 0:  # Rodando em bateria (Queda de energia!)
                    if not self.on_battery:
                        self.on_battery = True
                        if not self.silent:
                            self.add_log("🔌 QUEDA DE ENERGIA DETECTADA! PC rodando em bateria/nobreak.")
                    
                    # Se a bateria estiver abaixo de 20%, inicia o desligamento seguro
                    if battery_percent != 255 and battery_percent <= 20:
                        if not self.silent:
                            self.add_log(f"🚨 Bateria crítica ({battery_percent}%). Iniciando desligamento seguro...")
                        self.graceful_shutdown_due_to_power_loss()
                else:
                    if self.on_battery:
                        self.on_battery = False
                        if not self.silent:
                            self.add_log("🔌 ENERGIA ELÉTRICA RESTABELECIDA! Retornando ao modo AC.")
        except Exception:
            pass

    def graceful_shutdown_due_to_power_loss(self):
        # 1. Avisa por voz em segundo plano
        self.speak("Queda de energia detectada. Salvando vídeos e desligando o computador para proteção.")
        
        # 2. Finaliza as gravações ativas de forma limpa (salva buffers no disco)
        self.run_stop_sequence()
        
        # 3. Executa o comando de desligamento do Windows (com timer de 15s para segurança)
        try:
            subprocess.Popen("shutdown /s /t 15 /f /c \"Queda de Energia - Desligamento Seguro NVR\"", shell=True)
        except Exception:
            pass
            
        # 4. Encerra o aplicativo
        self.root.destroy()
        sys.exit(0)

    def check_process_go2rtc(self):
        try:
            output = subprocess.check_output('tasklist /FI "IMAGENAME eq go2rtc.exe"', shell=True, text=True)
            return "go2rtc.exe" in output
        except Exception:
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

    def check_process_recorder(self, lock_filename, stream_name):
        if self.recording_active.get(stream_name, False):
            return True
            
        lock_path = os.path.join(LOGS_DIR, lock_filename)
        if not os.path.exists(lock_path):
            return False
        try:
            with open(lock_path, "r") as f:
                content = f.read().strip()
            if not content.isdigit():
                return False
            pid = int(content)
            
            if pid == os.getpid():
                return False
                
            return self.is_pid_running_and_python(pid)
        except Exception:
            try:
                output = subprocess.check_output(
                    f'wmic process where "CommandLine like \'%gerenciador.pyw%\' and not CommandLine like \'%wmic%\'" get ProcessId',
                    shell=True,
                    text=True,
                    stderr=subprocess.DEVNULL
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
                if producers:
                    return "Sinal OK"
                else:
                    return "Conectando..."
            else:
                return "Não configurada"
        except Exception:
            return "Erro API"

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

    def check_last_recording(self, gdrive_ok, gdrive_path, stream_name):
        read_path = gdrive_path
        if not gdrive_ok or not os.path.exists(gdrive_path):
            read_path = os.path.join(PROJ_DIR, "backup_gravacoes", stream_name)
            
        if not os.path.exists(read_path):
            return "Nenhuma gravação encontrada."
            
        try:
            mp4_files = []
            for root_dir, _, files in os.walk(read_path):
                for f in files:
                    if f.endswith(".mp4"):
                        mp4_files.append(os.path.join(root_dir, f))
                        
            if not mp4_files:
                return "Sem gravações nesta pasta."
                
            last_file = max(mp4_files, key=os.path.getmtime)
            mtime = os.path.getmtime(last_file)
            mtime_dt = datetime.fromtimestamp(mtime)
            delta = datetime.now() - mtime_dt
            
            if delta.total_seconds() < 60:
                tempo = "agora mesmo"
            elif delta.total_seconds() < 3600:
                tempo = f"há {int(delta.total_seconds() // 60)} min"
            else:
                tempo = f"há {int(delta.total_seconds() // 3600)}h e {int((delta.total_seconds() % 3600) // 60)}min"
                
            filename = os.path.basename(last_file)
            origem = "Drive" if read_path == gdrive_path else "PC Local"
            return f"{filename}\n({origem} | Sincronizado: {tempo} às {mtime_dt.strftime('%H:%M:%S')})"
        except Exception:
            return "Erro ao ler pasta do Drive"

    def check_log_for_duplicate_error(self, log_file_path):
        if not os.path.exists(log_file_path):
            return None
        try:
            with open(log_file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
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
    def update_ui_states(self, go2rtc_ok, gdrive_ok, live_viewers, cam_states, backup_count, backup_size):
        with self.status_lock:
            if self.silent:
                return
                
            # Sincroniza o estado do botão com a realidade se não estiver em transição
            any_recording = any(state["grav_ok"] for state in cam_states.values())
            if hasattr(self, "button_state") and self.button_state not in ("STARTING", "STOPPING"):
                if any_recording:
                    if self.button_state != "RECORDING":
                        self.set_button_state("RECORDING")
                else:
                    if self.button_state != "STOPPED":
                        self.set_button_state("STOPPED")
                
            # 1. go2rtc status
            if go2rtc_ok:
                self.lbl_val_go2rtc.configure(text="ATIVO", fg=GREEN_COLOR)
                self.led_go2rtc.set_status(GREEN_COLOR, "#065F46")
            else:
                self.lbl_val_go2rtc.configure(text="INATIVO", fg=RED_COLOR)
                self.led_go2rtc.set_status(RED_COLOR, "#991B1B")
                
            # 2. gdrive status
            if gdrive_ok:
                self.lbl_val_gdrive.configure(text="CONECTADO", fg=GREEN_COLOR)
                self.led_gdrive.set_status(GREEN_COLOR, "#065F46")
            else:
                self.lbl_val_gdrive.configure(text="DESCONECTADO", fg=RED_COLOR)
                self.led_gdrive.set_status(RED_COLOR, "#991B1B")
                
            # 2.5. Backups pendentes status
            if backup_count == 0:
                self.lbl_val_backups.configure(text="NENHUM", fg=GREEN_COLOR)
                self.led_backups.set_status(GREEN_COLOR, "#065F46")
            else:
                size_mb = backup_size / (1024 * 1024)
                self.lbl_val_backups.configure(text=f"{backup_count} vídeo(s) ({size_mb:.1f} MB)", fg=ORANGE_COLOR)
                self.led_backups.set_status(ORANGE_COLOR, "#78350F")
                
            # 3. (Removido: lbl_viewers e lbl_val_web_monitor não existem mais na UI)
                
            # 4. Atualiza os cards das câmeras
            for stream, state in cam_states.items():
                if stream in self.camera_cards:
                    card = self.camera_cards[stream]
                    
                    # Sinal
                    if "Sinal OK" in state["signal"]:
                        card["lbl_sinal"].configure(text="SINAL OK", fg=GREEN_COLOR)
                        card["led_sinal"].set_status(GREEN_COLOR, "#065F46")
                    elif "Conectando" in state["signal"]:
                        card["lbl_sinal"].configure(text="CONECTANDO...", fg=ORANGE_COLOR)
                        card["led_sinal"].set_status(ORANGE_COLOR, "#78350F")
                    else:
                        card["lbl_sinal"].configure(text="SEM SINAL", fg=RED_COLOR)
                        card["led_sinal"].set_status(RED_COLOR, "#991B1B")
                        
                    # Gravação
                    if state["grav_ok"]:
                        card["lbl_grav"].configure(text="GRAVANDO", fg=GREEN_COLOR)
                        card["led_grav"].set_status(GREEN_COLOR, "#065F46")
                    elif state["duplicate_error"]:
                        card["lbl_grav"].configure(text="DUPLICADO (AVISO)", fg=ORANGE_COLOR)
                        card["led_grav"].set_status(ORANGE_COLOR, "#78350F")
                    else:
                        card["lbl_grav"].configure(text="PARADO", fg=RED_COLOR)
                        card["led_grav"].set_status(RED_COLOR, "#991B1B")
                        
                    # Web Stream
                    if "led_web" in card and "lbl_web" in card:
                        card["lbl_web"].configure(text=state["web_status"], fg=state["web_color"])
                        card["led_web"].set_status(state["web_color"], state["web_border"])
                        
                    card["lbl_sync"].configure(text=state["sync"])

    # ================= SINCRONIZADOR DE BACKUP EM SEGUNDO PLANO =================
    def background_sync_loop(self):
        while self.running_sync:
            time.sleep(30)
            
            if not os.path.exists(GDRIVE_ROOT):
                continue
                
            backup_dir = os.path.join(PROJ_DIR, "backup_gravacoes")
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
                        mp4_files = [f for f in files if f.endswith(".mp4")]
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
                                self.root.after(0, lambda fn=filename, s=stream: self.add_log(f"Subindo backup de {s.upper()}: {fn}..."))
                            
                            try:
                                shutil.copy2(local_filepath, dest_filepath)
                                if os.path.getsize(local_filepath) == os.path.getsize(dest_filepath):
                                    os.remove(local_filepath)
                                    if not self.silent:
                                        self.root.after(0, lambda fn=filename, s=stream: self.add_log(f"Sincronizado e apagado local: {fn}"))
                            except Exception as e:
                                if not self.silent:
                                    self.root.after(0, lambda fn=filename, err=str(e): self.add_log(f"Erro ao subir {fn}: {err}"))
                                    
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
        gdrive_dir = self.get_gdrive_dir(stream_name, index)
        lock_file = f"gravando_{stream_name}.lock"
        log_file = f"{stream_name}_erros.log"
        
        lock_path = os.path.join(LOGS_DIR, lock_file)
        log_path = os.path.join(LOGS_DIR, log_file)
        
        # Cria a trava local
        try:
            with open(lock_path, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass
            
        def escrever_log_cam(msg):
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            formatted = f"[{timestamp}] [{stream_name.upper()}] {msg}\n"
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(formatted)
            except Exception:
                pass
            if not self.silent:
                self.root.after(0, lambda: self.add_log(f"[{stream_name.upper()}] {msg}"))

        escrever_log_cam("=== INICIANDO TAREFA DE GRAVACAO INTERNA (REMUX THREAD) ===")
        
        # Testa a escrita no Drive para fallback
        try:
            os.makedirs(gdrive_dir, exist_ok=True)
            teste_path = os.path.join(gdrive_dir, ".teste_escrita")
            with open(teste_path, "w") as f:
                f.write("teste")
            os.remove(teste_path)
            pasta_final = gdrive_dir
        except Exception as e:
            pasta_fallback = os.path.join(PROJ_DIR, "backup_gravacoes", stream_name)
            os.makedirs(pasta_fallback, exist_ok=True)
            pasta_final = pasta_fallback
            escrever_log_cam(f"AVISO: Pasta do Drive indisponivel ({str(e)}). Usando backup local: {pasta_fallback}")

        # Loop principal da gravação
        while self.recording_active.get(stream_name, False):
            # Verifica duplicidade na rede
            conflito = self.verificar_duplicidade_rede_cam(gdrive_dir, stream_name)
            if conflito:
                escrever_log_cam(f"[ERRO_DUPLICADO] O computador {conflito['hostname']} ({conflito['ip']}) ja esta gravando esta camera.")
                break
                
            # Executa gravação do bloco
            status = self.gravar_bloco_cam(stream_name, pasta_final, gdrive_dir, escrever_log_cam)
            
            if status == "parar" or status == "duplicado":
                break
                
            if status == "erro" or status == "reconectar":
                escrever_log_cam("Aguardando 10 segundos antes de tentar reconectar...")
                for _ in range(20):
                    if not self.recording_active.get(stream_name, False):
                        break
                    time.sleep(0.5)
            elif status == "rotacionar":
                time.sleep(1)
                
        # Finalização e Limpeza
        if os.path.exists(lock_path):
            try:
                os.remove(lock_path)
            except Exception:
                pass
                
        try:
            gdrive_root = os.path.dirname(gdrive_dir)
            lock_name = f".active_recorder_{stream_name}.json"
            net_lock_path = os.path.join(gdrive_root, lock_name)
            if os.path.exists(net_lock_path):
                with open(net_lock_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("hostname") == socket.gethostname():
                    os.remove(net_lock_path)
        except Exception:
            pass
            
        escrever_log_cam("=== TAREFA DE GRAVACAO INTERNA ENCERRADA ===")

    def verificar_duplicidade_rede_cam(self, gdrive_dir, stream_name):
        gdrive_root = os.path.dirname(gdrive_dir)
        lock_name = f".active_recorder_{stream_name}.json"
        lock_path = os.path.join(gdrive_root, lock_name)
        
        if not os.path.exists(lock_path):
            return None
            
        try:
            with open(lock_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            last_heartbeat = data.get("timestamp", 0)
            hostname = data.get("hostname", "")
            ip = data.get("ip", "")
            
            current_time = time.time()
            my_hostname = socket.gethostname()
            
            if (current_time - last_heartbeat < 90) and (hostname != my_hostname):
                return {"hostname": hostname, "ip": ip}
        except Exception:
            pass
        return None

    def atualizar_heartbeat_cam(self, gdrive_dir, stream_name):
        gdrive_root = os.path.dirname(gdrive_dir)
        if not os.path.exists(gdrive_root):
            return
            
        lock_name = f".active_recorder_{stream_name}.json"
        lock_path = os.path.join(gdrive_root, lock_name)
        
        data = {
            "timestamp": time.time(),
            "hostname": socket.gethostname(),
            "ip": self.local_ip
        }
        
        try:
            with open(lock_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def obter_faixa_horario(self, dt):
        if dt.minute < 30:
            inicio = dt.replace(minute=0, second=0, microsecond=0)
            fim = dt.replace(minute=30, second=0, microsecond=0)
        else:
            inicio = dt.replace(minute=30, second=0, microsecond=0)
            fim = (dt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        return inicio, fim

    def gravar_bloco_cam(self, stream_name, pasta_final, gdrive_dir, escrever_log_cam):
        agora = datetime.now()
        inicio_bloco, fim_bloco = self.obter_faixa_horario(agora)
        
        data_dia = inicio_bloco.strftime("%Y-%m-%d")
        hora_inicio = inicio_bloco.strftime("%H-%M")
        hora_fim = fim_bloco.strftime("%H-%M")
        
        # Cria subpasta com a data do dia dentro do destino para melhor organização visual
        pasta_dia_final = os.path.join(pasta_final, data_dia)
        os.makedirs(pasta_dia_final, exist_ok=True)
        
        nome_arquivo = os.path.join(pasta_dia_final, f"camera_{data_dia}_{hora_inicio}_ate_{hora_fim}.mp4")
        
        # Gravação local temporária para evitar bloqueio e erros de sincronização no Google Drive
        temp_dir = os.path.join(PROJ_DIR, "gravando_temp", stream_name)
        os.makedirs(temp_dir, exist_ok=True)
        nome_temp = os.path.join(temp_dir, f"temp_camera_{data_dia}_{hora_inicio}_ate_{hora_fim}.mp4")
        
        escrever_log_cam(f"Iniciando gravacao temporaria do bloco: {os.path.basename(nome_arquivo)}")
        
        url = f"http://127.0.0.1:1984/api/stream.mp4?src={stream_name}"
        
        self.atualizar_heartbeat_cam(gdrive_dir, stream_name)
        last_heartbeat_time = time.time()
        
        status_ret = "reconectar"
        try:
            req = urllib.request.Request(url)
            response = urllib.request.urlopen(req, timeout=8)
            self.active_connections[stream_name] = response
            try:
                with open(nome_temp, "wb") as out_file:
                    while True:
                        if not self.recording_active.get(stream_name, False):
                            escrever_log_cam("Sinal de parada detectado.")
                            status_ret = "parar"
                            break
                            
                        # Batimento cardíaco e detecção de duplicidade
                        agora_ts = time.time()
                        if agora_ts - last_heartbeat_time >= 30:
                            conflito = self.verificar_duplicidade_rede_cam(gdrive_dir, stream_name)
                            if conflito:
                                escrever_log_cam(f"[ERRO_DUPLICADO] O computador {conflito['hostname']} ({conflito['ip']}) ja esta gravando.")
                                status_ret = "duplicado"
                                break
                            self.atualizar_heartbeat_cam(gdrive_dir, stream_name)
                            last_heartbeat_time = agora_ts
                            
                        # Rotação de blocos
                        if datetime.now() >= fim_bloco:
                            escrever_log_cam("Bloco anterior finalizado localmente.")
                            status_ret = "rotacionar"
                            break
                            
                        # Leitura do fluxo de vídeo
                        try:
                            chunk = response.read(64 * 1024)
                            if not chunk:
                                escrever_log_cam("Fim da stream detectado.")
                                break
                            out_file.write(chunk)
                        except socket.timeout:
                            continue
            finally:
                response.close()
        except Exception as e:
            escrever_log_cam(f"Erro na conexao com a stream: {str(e)}")
            status_ret = "erro"
        finally:
            self.active_connections.pop(stream_name, None)
            
        # Após fechar o arquivo temporário, movemos ele para a pasta definitiva (G: Drive ou backup local se offline)
        if os.path.exists(nome_temp):
            if os.path.getsize(nome_temp) > 0:
                try:
                    # Garante que a subpasta do dia exista no Drive/Rede
                    os.makedirs(pasta_dia_final, exist_ok=True)
                    shutil.move(nome_temp, nome_arquivo)
                    escrever_log_cam(f"Bloco movido com sucesso para a pasta definitiva: {os.path.join(data_dia, os.path.basename(nome_arquivo))}")
                except Exception as e_move:
                    escrever_log_cam(f"Erro ao mover bloco para {pasta_dia_final} ({str(e_move)}). Tentando salvar no backup local.")
                    try:
                        backup_dia_dir = os.path.join(PROJ_DIR, "backup_gravacoes", stream_name, data_dia)
                        os.makedirs(backup_dia_dir, exist_ok=True)
                        backup_arquivo = os.path.join(backup_dia_dir, f"camera_{data_dia}_{hora_inicio}_ate_{hora_fim}.mp4")
                        shutil.move(nome_temp, backup_arquivo)
                        escrever_log_cam(f"Bloco salvo no backup local de contingencia: {os.path.join(data_dia, os.path.basename(backup_arquivo))}")
                    except Exception as e_backup:
                        escrever_log_cam(f"ERRO CRITICO: Nao foi possivel salvar no backup local ({str(e_backup)})")
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
                    creationflags=subprocess.CREATE_NO_WINDOW
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
        # Encerra processos e threads anteriores
        self.run_stop_sequence()
        time.sleep(1.5)
        
        try:
            # 1. Liga a ponte RTSP go2rtc.exe se não estiver rodando
            if not self.check_process_go2rtc():
                if not self.silent:
                    self.add_log("Ligando Ponte RTSP (go2rtc.exe)...")
                go2rtc_dir = os.path.dirname(GO2RTC_EXE)
                env = os.environ.copy()
                env["PATH"] = go2rtc_dir + os.pathsep + env.get("PATH", "")
                subprocess.Popen(
                    [GO2RTC_EXE],
                    cwd=go2rtc_dir,
                    env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                time.sleep(2.5)
                
            # 2. Liga gravadores dinamicamente em threads separadas (NVR integrado)
            for idx, stream in enumerate(self.streams):
                if not self.silent:
                    self.add_log(f"Iniciando thread de gravacao da camera {stream.upper()}...")
                self.recording_active[stream] = True
                threading.Thread(
                    target=self.record_stream_thread, 
                    args=(stream, idx), 
                    daemon=True
                ).start()
                
            if not self.silent:
                self.root.after(0, lambda: self.add_log("Inicialização concluída em segundo plano."))
                self.root.after(0, lambda: self.set_button_state("RECORDING"))
                self.speak("Gravando")
        except Exception as e:
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
            self.speak("Gravação parada")

    def run_stop_sequence(self):
        # 1. Sinaliza parada para as threads locais
        for stream in self.streams:
            self.recording_active[stream] = False
            
        # Close all active connections before waiting for threads to exit
        for stream, conn in list(self.active_connections.items()):
            try:
                conn.close()
            except Exception:
                pass

        # 2. Lê os PIDs dos arquivos de lock e depois os remove
        pids = {}
        for stream in self.streams:
            lock_file = os.path.join(LOGS_DIR, f"gravando_{stream}.lock")
            if os.path.exists(lock_file):
                try:
                    with open(lock_file, "r") as f:
                        content = f.read().strip()
                    if content.isdigit():
                        pids[stream] = int(content)
                except Exception:
                    pass
                try:
                    os.remove(lock_file)
                except Exception:
                    pass
                    
        if not self.silent:
            self.root.after(0, lambda: self.add_log("Finalizando tarefas de gravação..."))
        
        # 3. Aguarda até 3 segundos para que as threads locais ou externas encerrem (ignora nosso próprio PID)
        my_pid = os.getpid()
        for _ in range(15):
            any_running = False
            for stream, pid in pids.items():
                if pid != my_pid and self.is_pid_running_and_python(pid):
                    any_running = True
            if not any_running:
                break
            time.sleep(0.2)
            
        # 4. Contingência: Finaliza à força qualquer instância externa de gravação (PID diferente do nosso)
        my_pid = os.getpid()
        for stream, pid in pids.items():
            if pid != my_pid and self.is_pid_running_and_python(pid):
                try:
                    os.kill(pid, 9)
                    if not self.silent:
                        self.root.after(0, lambda s=stream: self.add_log(f"Processo do gravador {s.upper()} finalizado."))
                except Exception:
                    pass

        # 5. Encerra go2rtc.exe
        subprocess.run('taskkill /F /IM go2rtc.exe', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def click_abrir_pasta(self):
        if os.path.exists(GDRIVE_ROOT):
            self.add_log("Abrindo pasta de câmeras do Google Drive...")
            os.startfile(GDRIVE_ROOT)
            self.flash_button(self.btn_open_folder, "✔️ Pasta Aberta!", "#10B981")
        else:
            self.add_log("ERRO: Pasta G:\\Meu Drive\\CAMERAS inacessível.")
            messagebox.showerror("Erro de Acesso", "Não foi possível abrir o Google Drive. Verifique se ele está rodando.")

    def click_monitor(self):
        self.add_log("Abrindo Monitor no navegador...")
        import webbrowser
        webbrowser.open("http://127.0.0.1:1984/visualizador.html")
        if hasattr(self, "btn_monitor"):
            self.flash_button(self.btn_monitor, "🌐 Abrindo...", "#3B82F6")

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
            messagebox.showerror("Erro", "O caminho do Drive/Rede não pode ser vazio.")
            return
            
        GDRIVE_ROOT = novo_caminho
        CONFIG["gdrive_root"] = GDRIVE_ROOT
        salvar_config(CONFIG)
        
        self.add_log(f"Caminho do Google Drive/Rede atualizado para: {GDRIVE_ROOT}")
        messagebox.showinfo("Caminho Salvo", f"O caminho foi atualizado com sucesso para:\n{GDRIVE_ROOT}")
        if hasattr(self, "btn_save_path"):
            self.flash_button(self.btn_save_path, "✔️ Salvo!", "#10B981")

    def click_escanear_corrompidos(self, show_popup=True):
        if not self.silent:
            self.add_log("🔄 Escaneamento automático de arquivos corrompidos em andamento...")
        threading.Thread(target=self.escanear_videos_corrompidos_thread, args=(show_popup,), daemon=True).start()

    def escanear_videos_corrompidos_thread(self, show_popup=True):
        ffmpeg_bin = os.path.join(PROJ_DIR, "go2rtc", "ffmpeg.exe")
        if not os.path.exists(ffmpeg_bin):
            if not self.silent:
                self.add_log("ERRO: ffmpeg.exe não encontrado para escanear.")
            return
            
        dirs_to_scan = []
        for idx, stream in enumerate(self.streams):
            dirs_to_scan.append((stream, os.path.join(PROJ_DIR, "backup_gravacoes", stream)))
            if os.path.exists(GDRIVE_ROOT):
                dirs_to_scan.append((stream, self.get_gdrive_dir(stream, idx)))
                 
        corrupted_count = 0
        scanned_count = 0
        
        for stream_name, directory in dirs_to_scan:
            if not os.path.exists(directory):
                continue
             
            try:
                # Escaneia recursivamente incluindo as subpastas organizadas por data
                for root_dir, _, files in os.walk(directory):
                    mp4_files = [f for f in files if f.endswith(".mp4")]
                    for filename in mp4_files:
                        filepath = os.path.join(root_dir, filename)
                        scanned_count += 1
                         
                        is_corrupt = False
                         
                        if os.path.getsize(filepath) == 0:
                            is_corrupt = True
                        else:
                            try:
                                # Executa verificação rápida no ffmpeg
                                cmd = f'"{ffmpeg_bin}" -v error -i "{filepath}" -t 1 -f null -'
                                res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
                                if res.returncode != 0:
                                    is_corrupt = True
                            except subprocess.TimeoutExpired:
                                is_corrupt = True
                            except Exception:
                                is_corrupt = True
                                 
                        if is_corrupt:
                            corrupted_count += 1
                            try:
                                os.remove(filepath)
                                if not self.silent:
                                    self.add_log(f"[EXCLUÍDO] Arquivo corrompido deletado: {filename}")
                                log_filepath = os.path.join(LOGS_DIR, "corrompidos_excluidos.log")
                                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                try:
                                    with open(log_filepath, "a", encoding="utf-8") as log_f:
                                        log_f.write(f"[{timestamp}] Deletado: {filepath}\n")
                                except Exception:
                                    pass
                            except Exception as e_del:
                                if not self.silent:
                                    self.add_log(f"Erro ao deletar {filename}: {str(e_del)}")
            except Exception:
                pass
                 
        if not self.silent:
            self.add_log(f"Escaneamento concluído. {scanned_count} arquivos analisados, {corrupted_count} corrompidos excluídos.")
            
            if show_popup:
                self.root.after(0, lambda: messagebox.showinfo("Scanner de Integridade", f"Varredura concluída!\n\nArquivos escaneados: {scanned_count}\nArquivos corrompidos deletados: {corrupted_count}\n\nOs arquivos corrompidos foram excluídos permanentemente para poupar espaço e limpar diretórios."))

    def flash_button(self, button, temp_text, temp_bg):
        old_text = button.cget("text")
        old_bg = button.cget("bg")
        button.configure(text=temp_text, bg=temp_bg)
        self.root.after(1500, lambda: button.configure(text=old_text, bg=old_bg))

    def trigger_periodic_scan(self):
        self.click_escanear_corrompidos(show_popup=False)
        self.root.after(10800000, self.trigger_periodic_scan)

    def trigger_periodic_diagnostics(self):
        """Executa diagnóstico automaticamente a cada 6 horas"""
        if not self.silent:
            self.add_log("🩺 Diagnóstico automático agendado em execução...")
        threading.Thread(target=self.run_diagnostics_sequence_auto, daemon=True).start()
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

    def graceful_shutdown(self):
        # Restaura as configurações originais de suspensão do Windows
        self.apply_prevent_sleep(False)
        
        # Para as conexões de vídeo das câmeras embutidas antes do encerramento
        if hasattr(self, "camera_widgets"):
            for cam_widget in self.camera_widgets.values():
                cam_widget.stop_stream()
                
        self.run_stop_sequence()
        time.sleep(1.0)
        if not self.silent:
            try:
                self.root.destroy()
            except Exception:
                pass
        sys.exit(0)

    def auto_provision_system(self):
        self.recuperar_videos_orfaos()
        threading.Thread(target=self.verificar_e_aplicar_firewall, daemon=True).start()

    def recuperar_videos_orfaos(self):
        temp_root = os.path.join(PROJ_DIR, "gravando_temp")
        if not os.path.exists(temp_root):
            return
        try:
            for stream in self.streams:
                stream_temp_dir = os.path.join(temp_root, stream)
                if not os.path.exists(stream_temp_dir):
                    continue
                files = [f for f in os.listdir(stream_temp_dir) if f.endswith(".mp4")]
                if not files:
                    continue
                
                dest_dir = os.path.join(PROJ_DIR, "backup_gravacoes", stream)
                os.makedirs(dest_dir, exist_ok=True)
                
                for filename in files:
                    temp_file = os.path.join(stream_temp_dir, filename)
                    if os.path.exists(temp_file):
                        if os.path.getsize(temp_file) > 0:
                            nome_novo = filename.replace("temp_camera_", "recuperado_camera_")
                            dest_file = os.path.join(dest_dir, nome_novo)
                            try:
                                shutil.move(temp_file, dest_file)
                                if not self.silent:
                                    self.add_log(f"Arquivo orfao recuperado da queda de energia: {nome_novo}")
                            except Exception:
                                pass
                        else:
                            try:
                                os.remove(temp_file)
                            except Exception:
                                pass
        except Exception:
            pass

    def verificar_e_aplicar_firewall(self):
        marker_file = os.path.join(LOGS_DIR, ".firewall_configured")
        if os.path.exists(marker_file):
            return
            
        try:
            res = subprocess.run(
                'netsh advfirewall firewall show rule name="Camera Farmacia - API (1984)"',
                shell=True, capture_output=True, text=True
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
        url_visualizador = "https://raw.githubusercontent.com/WilliYY/camerafarmacia/main/visualizador.html"
        
        try:
            req = urllib.request.Request(url_gerenciador, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8) as conn:
                content = conn.read().decode('utf-8', errors='ignore')
                
            import re
            match = re.search(r'VERSION\s*=\s*["\']([^"\']+)["\']', content)
            if match:
                online_version = match.group(1)
                if self.is_version_newer(online_version, VERSION):
                    self.add_log(f"Nova versao v{online_version} encontrada! (Versao local: v{VERSION})")
                    self.root.after(0, lambda: self.prompt_update(online_version, url_gerenciador, url_visualizador))
            else:
                self.add_log("Nao foi possivel identificar a versao remota.")
        except Exception as e:
            self.add_log(f"Erro ao buscar atualizacoes: {str(e)}")

        # Agenda nova verificação automática para daqui a 1 hora
        self.root.after(3600000, self.trigger_periodic_update)

    def trigger_periodic_update(self):
        threading.Thread(target=self.check_for_updates_thread, daemon=True).start()
            
    def prompt_update(self, online_version, url_gerenciador, url_visualizador):
        msg = f"Uma nova versao (v{online_version}) esta disponivel no GitHub!\n\nSua versao local e v{VERSION}.\n\nDeseja atualizar o sistema automaticamente agora?"
        if messagebox.askyesno("Atualizacao Disponivel", msg):
            threading.Thread(target=self.run_auto_update, args=(url_gerenciador, url_visualizador), daemon=True).start()
            
    def run_auto_update(self, url_gerenciador, url_visualizador):
        self.add_log("Iniciando atualizacao automatica...")
        
        gerenciador_temp = os.path.join(PROJ_DIR, "gerenciador.pyw.tmp")
        visualizador_temp = os.path.join(PROJ_DIR, "visualizador.html.tmp")
        
        try:
            req_g = urllib.request.Request(url_gerenciador, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req_g, timeout=10) as conn:
                g_content = conn.read()
                
            req_v = urllib.request.Request(url_visualizador, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req_v, timeout=10) as conn:
                v_content = conn.read()
                
            with open(gerenciador_temp, "wb") as f:
                f.write(g_content)
            with open(visualizador_temp, "wb") as f:
                f.write(v_content)
                
            self.add_log("Parando gravacoes para aplicar atualizacao...")
            self.run_stop_sequence()
            time.sleep(1.0)
            
            # Técnica de rename no Windows para evitar erro de arquivo travado
            dest_gerenciador = os.path.join(PROJ_DIR, "gerenciador.pyw")
            old_gerenciador = os.path.join(PROJ_DIR, "gerenciador.pyw.old")
            
            if os.path.exists(old_gerenciador):
                try:
                    os.remove(old_gerenciador)
                except Exception:
                    pass
                    
            try:
                os.rename(dest_gerenciador, old_gerenciador)
            except Exception:
                pass
                
            shutil.move(gerenciador_temp, dest_gerenciador)
            
            # Para o visualizador.html não precisa de rename pois ele não está travado em execução
            dest_visualizador = os.path.join(PROJ_DIR, "visualizador.html")
            if os.path.exists(dest_visualizador):
                try:
                    os.remove(dest_visualizador)
                except Exception:
                    pass
            shutil.move(visualizador_temp, dest_visualizador)
            
            self.add_log("Sistema atualizado com sucesso!")
            self.root.after(0, lambda: messagebox.showinfo("Atualizado", "O sistema foi atualizado com sucesso para a nova versao!\n\nO aplicativo sera reiniciado agora."))
            
            # Restart
            subprocess.Popen([sys.executable, os.path.join(PROJ_DIR, "gerenciador.pyw")])
            self.root.after(0, self.root.quit)
        except Exception as e:
            self.add_log(f"ERRO durante a atualizacao: {str(e)}")
            for temp_file in [gerenciador_temp, visualizador_temp]:
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except Exception:
                        pass
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
            "Configuração go2rtc.yaml": os.path.join(PROJ_DIR, "go2rtc", "go2rtc.yaml"),
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

        # 3. Google Drive (G:)
        log.append("\n--- [3] ARMAZENAMENTO NO GOOGLE DRIVE ---")
        if os.path.exists(GDRIVE_ROOT):
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
            log.append(f" - ERRO: Diretório Raiz G:\\Meu Drive\\CAMERAS não foi encontrado!")

        # 4. Processos em Execução
        log.append("\n--- [4] PROCESSOS EM EXECUÇÃO ---")
        go2rtc_running = self.check_process_go2rtc()
        log.append(f" - Processo go2rtc.exe: {'RODANDO' if go2rtc_running else 'PARADO'}")
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
                log.append(f" - Configuração de streams na API: {json.dumps(data, indent=2)}")
        except Exception as e:
            log.append(f" - Porta API (1984): FECHADA ou erro ao consultar: {str(e)}")

        # 6. Ambiente Python
        log.append("\n--- [6] AMBIENTE DO SISTEMA ---")
        log.append(f" - Versão do Python: {sys.version}")

        diag_file = os.path.join(PROJ_DIR, "diagnostico.txt")
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
            "Configuração go2rtc.yaml": os.path.join(PROJ_DIR, "go2rtc", "go2rtc.yaml"),
        }
        for name, path in files_to_check.items():
            exists = os.path.exists(path)
            status = "OK" if exists else "NÃO ENCONTRADO"
            log.append(f" - {name}: {status}")
        
        log.append("\n--- [2] PROCESSOS ---")
        go2rtc_running = self.check_process_go2rtc()
        log.append(f" - go2rtc.exe: {'RODANDO' if go2rtc_running else 'PARADO'}")
        for stream in self.streams:
            c_running = self.check_process_recorder(f"gravando_{stream}.lock", stream)
            log.append(f" - Gravador {stream.upper()}: {'RODANDO' if c_running else 'PARADO'}")
        
        log.append(f"\n--- [3] Google Drive ---")
        log.append(f" - Disponível: {'SIM' if os.path.exists(GDRIVE_ROOT) else 'NÃO'}")
        
        log.append(f"\n--- [4] AMBIENTE ---")
        log.append(f" - Python: {sys.version}")
        log.append(f" - IP Local: {self.local_ip}")
        
        diag_file = os.path.join(PROJ_DIR, "diagnostico.txt")
        try:
            with open(diag_file, "w", encoding="utf-8") as f:
                f.write("\n".join(log))
            if not self.silent:
                self.root.after(0, lambda: self.add_log("Diagnóstico automático concluído com sucesso."))
        except Exception as e:
            if not self.silent:
                self.root.after(0, lambda: self.add_log(f"Erro ao salvar diagnóstico automático: {str(e)}"))

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Gerenciador NVR Câmeras Farmácia")
    parser.add_argument("--silent", action="store_true", help="Inicia o sistema de gravação em segundo plano sem abrir a janela")
    args_cli = parser.parse_args()
    
    # Garante que as dependências binárias existam antes de inicializar o painel
    PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
    verificar_e_baixar_dependencias(PROJ_DIR, silent=args_cli.silent)
    
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
        
    root = tk.Tk()
    if args_cli.silent:
        root.withdraw() # Esconde a janela principal!
        app = CameraManagerApp(root, silent=True)
    else:
        app = CameraManagerApp(root, silent=False)
        
    root.mainloop()
