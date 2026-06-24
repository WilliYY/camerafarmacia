import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import os
import sys
import socket
import urllib.request
import json
import threading
import time
import ctypes
import shutil
from datetime import datetime, timedelta

# Versão do Sistema (usada para o auto-update)
VERSION = "3.8.3"

# Configurações do Projeto
PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
GO2RTC_EXE = os.path.join(PROJ_DIR, "go2rtc", "go2rtc.exe")
LOGS_DIR = os.path.join(PROJ_DIR, "logs")

# Garante a existência das pastas do projeto
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(os.path.join(PROJ_DIR, "backup_gravacoes"), exist_ok=True)
os.makedirs(os.path.join(PROJ_DIR, "gravando_temp"), exist_ok=True)

GDRIVE_ROOT = r"G:\Meu Drive\CAMERAS"

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

class CameraManagerApp:
    def __init__(self, root, silent=False):
        self.root = root
        self.silent = silent
        
        # Variáveis de Gravação em Memória (NVR Integrado)
        self.recording_active = {}
        self.status_lock = threading.Lock()
        self.alerted_duplicates = {} # Evita exibir alerta popup repetidamente
        
        self.streams = self.parse_streams()
        self.local_ip = self.get_local_ip()
        
        # 1. Configura título e layout se não estiver em modo silencioso
        if not self.silent:
            self.root.title(f"Controle das Câmeras - Farmácia (NVR Unificado v{VERSION})")
            self.root.geometry("680x700")
            self.root.configure(bg=BG_COLOR)
            self.root.resizable(False, False)
            
            self.setup_styles()
            self.create_widgets()
            
            # Thread de verificação de atualizações no GitHub
            threading.Thread(target=self.check_for_updates_thread, daemon=True).start()
            
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
        # 1. HEADER / CABEÇALHO
        header_frame = tk.Frame(self.root, bg=BG_COLOR, pady=10)
        header_frame.pack(fill="x", padx=20)
        
        title_label = tk.Label(
            header_frame, 
            text=" 🎥 NVR Câmeras Farmácia", 
            font=("Segoe UI", 16, "bold"), 
            fg=TEXT_COLOR, 
            bg=BG_COLOR
        )
        title_label.pack(side="left")
        
        subtitle_label = tk.Label(
            header_frame, 
            text=f"v{VERSION} Unificado", 
            font=("Segoe UI", 9, "bold"), 
            fg=ACCENT_COLOR, 
            bg=BG_COLOR
        )
        subtitle_label.pack(side="left", padx=10, pady=6)
        
        self.lbl_viewers = tk.Label(
            header_frame,
            text="👁️ Assistindo: 0",
            font=("Segoe UI", 9, "bold"),
            fg=ORANGE_COLOR,
            bg=BG_COLOR
        )
        self.lbl_viewers.pack(side="right", pady=6)
        
        # Divisor horizontal elegante
        separator = tk.Frame(self.root, height=1, bg="#1F2937")
        separator.pack(fill="x", padx=20)

        # 2. CARDS GLOBAIS (SERVIÇOS E REDE)
        top_cards_frame = tk.Frame(self.root, bg=BG_COLOR, pady=6)
        top_cards_frame.pack(fill="x", padx=20)
        
        top_cards_frame.columnconfigure(0, weight=1, uniform="top_grid")
        top_cards_frame.columnconfigure(1, weight=1, uniform="top_grid")
        
        # Card 1: Serviços Globais
        self.card_global = tk.Frame(top_cards_frame, bg=CARD_COLOR, bd=1, relief="flat", padx=15, pady=8)
        self.card_global.grid(row=0, column=0, padx=4, pady=4, sticky="nsew")
        tk.Label(self.card_global, text="Status dos Serviços", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=CARD_COLOR).pack(anchor="w")
        
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
        
        # Card 2: Endereço IP
        self.card_network = tk.Frame(top_cards_frame, bg=CARD_COLOR, bd=1, relief="flat", padx=15, pady=8)
        self.card_network.grid(row=0, column=1, padx=4, pady=4, sticky="nsew")
        tk.Label(self.card_network, text="Acesso na Rede Local (IP)", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=CARD_COLOR).pack(anchor="w")
        tk.Label(self.card_network, text=f"IP: {self.local_ip}", font=("Segoe UI", 11, "bold"), fg=TEXT_COLOR, bg=CARD_COLOR).pack(anchor="w", pady=2)
        
        self.lbl_link = tk.Label(
            self.card_network, 
            text="Copiar link do painel Web", 
            font=("Segoe UI", 8, "bold", "underline"), 
            fg=ACCENT_COLOR, 
            cursor="hand2", 
            bg=CARD_COLOR
        )
        self.lbl_link.pack(anchor="w")
        self.lbl_link.bind("<Button-1>", lambda e: self.copy_link_to_clipboard())

        # 3. GRID DINÂMICO DE CÂMERAS
        self.cameras_main_frame = tk.Frame(self.root, bg=BG_COLOR)
        self.cameras_main_frame.pack(fill="both", expand=True, padx=20, pady=4)
        
        self.cameras_main_frame.columnconfigure(0, weight=1, uniform="cam_grid")
        self.cameras_main_frame.columnconfigure(1, weight=1, uniform="cam_grid")
        
        self.camera_cards = {}
        col = 0
        row = 0
        
        for idx, stream in enumerate(self.streams):
            card = tk.Frame(self.cameras_main_frame, bg=CARD_COLOR, bd=1, relief="flat", padx=12, pady=8)
            card.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")
            
            # Título da Câmera
            tk.Label(card, text=f"📷 CÂMERA: {stream.upper()}", font=("Segoe UI", 9, "bold"), fg=ACCENT_COLOR, bg=CARD_COLOR).pack(anchor="w", pady=(0, 4))
            
            # Sinal da Câmera
            row_sinal = tk.Frame(card, bg=CARD_COLOR)
            row_sinal.pack(anchor="w", pady=1)
            led_sinal = StatusLED(row_sinal, size=10, bg_color=CARD_COLOR)
            led_sinal.pack(side="left", padx=(0, 6), pady=2)
            tk.Label(row_sinal, text="Sinal: ", font=("Segoe UI", 9), fg=TEXT_COLOR, bg=CARD_COLOR).pack(side="left")
            lbl_sinal = tk.Label(row_sinal, text="Verificando...", font=("Segoe UI", 9, "bold"), fg=ORANGE_COLOR, bg=CARD_COLOR)
            lbl_sinal.pack(side="left")
            
            # Gravação
            row_grav = tk.Frame(card, bg=CARD_COLOR)
            row_grav.pack(anchor="w", pady=1)
            led_grav = StatusLED(row_grav, size=10, bg_color=CARD_COLOR)
            led_grav.pack(side="left", padx=(0, 6), pady=2)
            tk.Label(row_grav, text="Gravação: ", font=("Segoe UI", 9), fg=TEXT_COLOR, bg=CARD_COLOR).pack(side="left")
            lbl_grav = tk.Label(row_grav, text="Verificando...", font=("Segoe UI", 9, "bold"), fg=ORANGE_COLOR, bg=CARD_COLOR)
            lbl_grav.pack(side="left")
            
            # Última gravação/Sync
            lbl_sync = tk.Label(card, text="Buscando arquivos...", font=("Segoe UI", 8, "bold"), fg=TEXT_MUTED, bg=CARD_COLOR, justify="left", wraplength=280)
            lbl_sync.pack(anchor="w", pady=(4, 0))
            
            # Salva referências para atualização
            self.camera_cards[stream] = {
                "led_sinal": led_sinal,
                "lbl_sinal": lbl_sinal,
                "led_grav": led_grav,
                "lbl_grav": lbl_grav,
                "lbl_sync": lbl_sync
            }
            
            col += 1
            if col >= 2:
                col = 0
                row += 1

        # 4. CONTROLES / BOTÕES
        btn_frame = tk.Frame(self.root, bg=BG_COLOR, pady=6)
        btn_frame.pack(fill="x", padx=20)
        
        self.btn_start = tk.Button(
            btn_frame, 
            text=" ▶️ Iniciar Todas as Gravações", 
            font=("Segoe UI", 11, "bold"), 
            fg="#FFFFFF", 
            bg="#059669", 
            activebackground="#047857", 
            activeforeground="#FFFFFF",
            bd=0, 
            cursor="hand2",
            padx=15, 
            pady=6,
            command=self.click_iniciar
        )
        self.btn_start.pack(side="left", padx=4, expand=True, fill="x")
        
        self.btn_stop = tk.Button(
            btn_frame, 
            text=" ⏹️ Parar Todas as Gravações", 
            font=("Segoe UI", 11, "bold"), 
            fg="#FFFFFF", 
            bg="#DC2626", 
            activebackground="#B91C1C", 
            activeforeground="#FFFFFF",
            bd=0, 
            cursor="hand2",
            padx=15, 
            pady=6,
            command=self.click_parar
        )
        self.btn_stop.pack(side="left", padx=4, expand=True, fill="x")

        # Ações extras
        actions_frame = tk.Frame(self.root, bg=BG_COLOR, pady=2)
        actions_frame.pack(fill="x", padx=20)
        
        self.btn_diag = tk.Button(
            actions_frame, 
            text=" 🩺 Gerar Diagnóstico", 
            font=("Segoe UI", 9, "bold"), 
            fg=TEXT_COLOR, 
            bg="#1F2937", 
            activebackground="#374151", 
            activeforeground=TEXT_COLOR,
            bd=0, 
            cursor="hand2",
            padx=10, 
            pady=5,
            command=self.click_diagnostico
        )
        self.btn_diag.pack(side="left", padx=4, expand=True, fill="x")
        
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
        self.btn_open_folder.pack(side="left", padx=4, expand=True, fill="x")
        
        self.btn_monitor = tk.Button(
            actions_frame, 
            text=" 📺 Monitor Lado a Lado", 
            font=("Segoe UI", 9, "bold"), 
            fg=TEXT_COLOR, 
            bg="#1F2937", 
            activebackground="#374151", 
            activeforeground=TEXT_COLOR,
            bd=0, 
            cursor="hand2",
            padx=10, 
            pady=5,
            command=self.click_monitor
        )
        self.btn_monitor.pack(side="left", padx=4, expand=True, fill="x")

        # Inicialização automática
        startup_frame = tk.Frame(self.root, bg=BG_COLOR, pady=2)
        startup_frame.pack(fill="x", padx=20)
        
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

        # 5. LOG DE EVENTOS (CONSOLE)
        log_title_frame = tk.Frame(self.root, bg=BG_COLOR)
        log_title_frame.pack(fill="x", padx=25, pady=(4,0))
        tk.Label(log_title_frame, text="Log de Eventos:", font=("Segoe UI", 8, "bold"), fg=TEXT_MUTED, bg=BG_COLOR).pack(anchor="w")
        
        self.txt_log = tk.Text(self.root, height=5, bg="#030712", fg="#34D399", font=("Consolas", 9), bd=0, padx=10, pady=5)
        self.txt_log.pack(fill="x", padx=20, pady=(2, 6))
        self.txt_log.configure(state="disabled")

    # ================= LOG DE EVENTOS =================
    def add_log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted = f"[{timestamp}] {msg}\n"
        self.txt_log.configure(state="normal")
        self.txt_log.insert(tk.END, formatted)
        self.txt_log.see(tk.END)
        self.txt_log.configure(state="disabled")

    def copy_link_to_clipboard(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(f"http://{self.local_ip}:1984")
        self.add_log("Link Web copiado para a área de transferência!")
        messagebox.showinfo("Copiado", f"O link http://{self.local_ip}:1984 foi copiado com sucesso!")

    # ================= MONITOR LOOP (THREAD SEPARADA) =================
    def monitor_loop(self):
        while self.running_monitor:
            # 1. Verifica se go2rtc está ativo
            go2rtc_ok = self.check_process_go2rtc()
            
            # 2. Verifica se o Google Drive está conectado
            gdrive_ok = os.path.exists(GDRIVE_ROOT)
            
            # 3. Coleta visualizadores ao vivo
            live_viewers = self.get_live_viewers(go2rtc_ok)
            
            # 3.5. Coleta estatísticas de backups locais pendentes na pasta do projeto
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
                
                cam_states[stream] = {
                    "grav_ok": c_grav_ok,
                    "signal": c_signal_str,
                    "sync": last_file_str,
                    "duplicate_error": duplicate_msg is not None
                }
            
            # Atualiza a interface (se não estiver em modo silencioso)
            if not self.silent:
                self.root.after(0, self.update_ui_states, go2rtc_ok, gdrive_ok, live_viewers, cam_states, backup_count, backup_size)
            
            # Dorme por 3 segundos
            time.sleep(3)

    def get_backup_stats(self):
        backup_dir = os.path.join(PROJ_DIR, "backup_gravacoes")
        if not os.path.exists(backup_dir):
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
        return total_files, total_size

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
            with urllib.request.urlopen("http://localhost:1984/api/streams", timeout=1.0) as conn:
                data = json.loads(conn.read().decode())
                if stream_name in data:
                    producers = data[stream_name].get("producers", [])
                    if producers:
                        return "Sinal OK"
                    else:
                        return "Conectando..."
                else:
                    return "Não configurada"
        except Exception:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            try:
                s.connect(('127.0.0.1', 1984))
                s.close()
                return "Porta OK"
            except Exception:
                return "Erro API"

    def check_last_recording(self, gdrive_ok, gdrive_path, stream_name):
        read_path = gdrive_path
        if not gdrive_ok or not os.path.exists(gdrive_path):
            read_path = os.path.join(PROJ_DIR, "backup_gravacoes", stream_name)
            
        if not os.path.exists(read_path):
            return "Nenhuma gravação encontrada."
            
        try:
            files = [os.path.join(read_path, f) for f in os.listdir(read_path) if f.endswith(".mp4")]
            if not files:
                return "Sem gravações nesta pasta."
            last_file = max(files, key=os.path.getmtime)
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
            with urllib.request.urlopen("http://localhost:1984/api/streams", timeout=1.0) as conn:
                data = json.loads(conn.read().decode())
            for stream_name, stream_data in data.items():
                consumers = stream_data.get("consumers", [])
                for consumer in consumers:
                    addr = consumer.get("remote_addr", "")
                    ua = consumer.get("user_agent", "").lower()
                    
                    if "127.0.0.1" in addr or "[::1]" in addr or "localhost" in addr:
                        continue
                    if "lavf" in ua:
                        continue
                        
                    ip = addr.split(":")[0] if ":" in addr else addr
                    browser = "Navegador"
                    if "chrome" in ua: browser = "Chrome"
                    elif "safari" in ua and "chrome" not in ua: browser = "Safari"
                    elif "firefox" in ua: browser = "Firefox"
                    
                    viewers.append(f"{ip} ({browser})")
        except Exception:
            pass
        return viewers

    # ================= ATUALIZAÇÃO DA GUI =================
    def update_ui_states(self, go2rtc_ok, gdrive_ok, live_viewers, cam_states, backup_count, backup_size):
        with self.status_lock:
            if self.silent:
                return
                
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
                
            # 3. Atualiza os visualizadores ao vivo
            if live_viewers:
                self.lbl_viewers.configure(text=f"👁️ Assistindo: {len(live_viewers)} ({', '.join(live_viewers)})", fg=GREEN_COLOR)
            else:
                self.lbl_viewers.configure(text="👁️ Assistindo: 0", fg=TEXT_MUTED)
                
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
                        
                    files = [f for f in os.listdir(stream_backup_dir) if f.endswith(".mp4")]
                    if not files:
                        continue
                        
                    gdrive_dest = self.get_gdrive_dir(stream, idx)
                    os.makedirs(gdrive_dest, exist_ok=True)
                    
                    # Testa permissão de escrita no Drive
                    teste_path = os.path.join(gdrive_dest, ".sync_test")
                    try:
                        with open(teste_path, "w") as tf:
                            tf.write("test")
                        os.remove(teste_path)
                    except Exception:
                        continue
                        
                    for filename in files:
                        local_filepath = os.path.join(stream_backup_dir, filename)
                        dest_filepath = os.path.join(gdrive_dest, filename)
                        
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
        
        nome_arquivo = os.path.join(pasta_final, f"camera_{data_dia}_{hora_inicio}_ate_{hora_fim}.mp4")
        
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
            with urllib.request.urlopen(req, timeout=15) as response:
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
        except Exception as e:
            escrever_log_cam(f"Erro na conexao com a stream: {str(e)}")
            status_ret = "erro"
            
        # Após fechar o arquivo temporário, movemos ele para a pasta definitiva (G: Drive ou backup local se offline)
        if os.path.exists(nome_temp):
            if os.path.getsize(nome_temp) > 0:
                try:
                    # Garante que a pasta final exista
                    os.makedirs(pasta_final, exist_ok=True)
                    shutil.move(nome_temp, nome_arquivo)
                    escrever_log_cam(f"Bloco movido com sucesso para a pasta definitiva: {os.path.basename(nome_arquivo)}")
                except Exception as e_move:
                    escrever_log_cam(f"Erro ao mover bloco para {pasta_final} ({str(e_move)}). Tentando salvar no backup local.")
                    try:
                        backup_dir = os.path.join(PROJ_DIR, "backup_gravacoes", stream_name)
                        os.makedirs(backup_dir, exist_ok=True)
                        backup_arquivo = os.path.join(backup_dir, os.path.basename(nome_arquivo))
                        shutil.move(nome_temp, backup_arquivo)
                        escrever_log_cam(f"Bloco salvo no backup local de contingencia: {os.path.basename(nome_arquivo)}")
                    except Exception as e_backup:
                        escrever_log_cam(f"ERRO CRITICO: Nao foi possivel salvar no backup local ({str(e_backup)})")
            else:
                try:
                    os.remove(nome_temp)
                except Exception:
                    pass
                    
        return status_ret

    # ================= CLIQUES DE BOTÕES =================
    def click_iniciar(self):
        if not self.silent:
            self.add_log("Iniciando gravação do sistema...")
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
                subprocess.Popen(
                    [GO2RTC_EXE],
                    cwd=os.path.dirname(GO2RTC_EXE),
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
        except Exception as e:
            if not self.silent:
                self.root.after(0, lambda: self.add_log(f"ERRO ao iniciar gravação: {str(e)}"))
                self.root.after(0, lambda: messagebox.showerror("Erro ao Iniciar", f"Não foi possível iniciar o serviço:\n{str(e)}"))

    def click_parar(self):
        if not self.silent:
            self.add_log("Parando gravação e finalizando processos...")
        threading.Thread(target=self.run_stop_sequence_verbose, daemon=True).start()

    def run_stop_sequence(self):
        # 1. Sinaliza parada para as threads locais
        for stream in self.streams:
            self.recording_active[stream] = False
            
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
        
        # 3. Aguarda até 3 segundos para que as threads locais ou externas encerrem
        for _ in range(15):
            any_running = False
            for stream, pid in pids.items():
                if self.is_pid_running_and_python(pid):
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

    def run_stop_sequence_verbose(self):
        self.run_stop_sequence()
        if not self.silent:
            self.root.after(0, lambda: self.add_log("Gravação interrompida. Todos os serviços parados!"))

    def click_abrir_pasta(self):
        if os.path.exists(GDRIVE_ROOT):
            self.add_log("Abrindo pasta de câmeras do Google Drive...")
            os.startfile(GDRIVE_ROOT)
        else:
            self.add_log("ERRO: Pasta G:\\Meu Drive\\CAMERAS inacessível.")
            messagebox.showerror("Erro de Acesso", "Não foi possível abrir o Google Drive. Verifique se ele está rodando.")

    def click_monitor(self):
        self.add_log("Abrindo Monitor no navegador...")
        import webbrowser
        webbrowser.open("http://127.0.0.1:1984/visualizador.html")

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
        except Exception as e:
            self.add_log(f"ERRO ao configurar inicialização: {str(e)}")
            messagebox.showerror("Erro de Configuração", f"Não foi possível salvar o arquivo de inicialização:\n{str(e)}")

    # ================= SISTEMA DE ATUALIZAÇÃO AUTOMÁTICA =================
    def check_for_updates_thread(self):
        time.sleep(5)
        self.add_log("Buscando atualizacoes no GitHub...")
        
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
                if online_version != VERSION:
                    self.add_log(f"Nova versao v{online_version} encontrada! (Versao local: v{VERSION})")
                    self.root.after(0, lambda: self.prompt_update(online_version, url_gerenciador, url_visualizador))
                else:
                    self.add_log(f"Sistema atualizado (v{VERSION}).")
            else:
                self.add_log("Nao foi possivel identificar a versao remota.")
        except Exception as e:
            self.add_log(f"Erro ao buscar atualizacoes: {str(e)}")
            
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
            
            shutil.move(gerenciador_temp, os.path.join(PROJ_DIR, "gerenciador.pyw"))
            shutil.move(visualizador_temp, os.path.join(PROJ_DIR, "visualizador.html"))
            
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
            with urllib.request.urlopen("http://localhost:1984/api/streams", timeout=1.0) as conn:
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

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Gerenciador NVR Câmeras Farmácia")
    parser.add_argument("--silent", action="store_true", help="Inicia o sistema de gravação em segundo plano sem abrir a janela")
    args_cli = parser.parse_args()
    
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
