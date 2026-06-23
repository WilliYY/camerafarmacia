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
from datetime import datetime

# Configurações do Projeto
PROJ_DIR = r"C:\Users\Thiesen\Desktop\camera farmacia"
GO2RTC_EXE = os.path.join(PROJ_DIR, "go2rtc", "go2rtc.exe")
RECORDER_SCRIPT = os.path.join(PROJ_DIR, "gravador_camera.py")

GDRIVE_ROOT = r"G:\Meu Drive\CAMERAS"
GDRIVE_DIR1 = os.path.join(GDRIVE_ROOT, "CAMERA 1 FARMACIA")
GDRIVE_DIR2 = os.path.join(GDRIVE_ROOT, "CAMERA 2 FARMACIA")

LOCK_FILE1 = os.path.join(PROJ_DIR, "gravando_c1.lock")
LOCK_FILE2 = os.path.join(PROJ_DIR, "gravando_c2.lock")

LOG_FILE1 = os.path.join(PROJ_DIR, "c1_erros.log")
LOG_FILE2 = os.path.join(PROJ_DIR, "c2_erros.log")

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
    def __init__(self, root):
        self.root = root
        self.root.title("Controle da Câmera - Farmácia (Duplo)")
        self.root.geometry("660x650")
        self.root.configure(bg=BG_COLOR)
        self.root.resizable(False, False)
        
        # Obtém o IP local da rede de forma dinâmica
        self.local_ip = self.get_local_ip()
        
        # Variáveis de Controle
        self.status_lock = threading.Lock()
        
        # Inicializa a Interface
        self.setup_styles()
        self.create_widgets()
        
        # Inicia a thread de monitoramento em tempo real
        self.running_monitor = True
        self.monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.monitor_thread.start()
        
        self.add_log("Painel Duplo Premium iniciado. Monitoramento ativo.")

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
        header_frame = tk.Frame(self.root, bg=BG_COLOR, pady=12)
        header_frame.pack(fill="x", padx=20)
        
        title_label = tk.Label(
            header_frame, 
            text=" 🎥 Painel Câmeras Farmácia", 
            font=("Segoe UI", 16, "bold"), 
            fg=TEXT_COLOR, 
            bg=BG_COLOR
        )
        title_label.pack(side="left")
        
        subtitle_label = tk.Label(
            header_frame, 
            text="v2.5 Duplo Premium", 
            font=("Segoe UI", 9, "bold"), 
            fg=ACCENT_COLOR, 
            bg=BG_COLOR
        )
        subtitle_label.pack(side="left", padx=10, pady=6)
        
        # Divisor horizontal elegante
        separator = tk.Frame(self.root, height=1, bg="#1F2937")
        separator.pack(fill="x", padx=20)

        # 2. STATUS GRID (2x2 CARDS)
        status_main_frame = tk.Frame(self.root, bg=BG_COLOR, pady=8)
        status_main_frame.pack(fill="x", padx=20)
        
        status_main_frame.columnconfigure(0, weight=1, uniform="grid")
        status_main_frame.columnconfigure(1, weight=1, uniform="grid")
        
        # Card 1: Serviços Globais (Ponte & Google Drive)
        self.card_global = tk.Frame(status_main_frame, bg=CARD_COLOR, bd=1, relief="flat", padx=15, pady=10)
        self.card_global.grid(row=0, column=0, padx=4, pady=4, sticky="nsew")
        tk.Label(self.card_global, text="Status dos Serviços", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=CARD_COLOR).pack(anchor="w")
        
        # Linha Ponte RTSP
        row_go2rtc = tk.Frame(self.card_global, bg=CARD_COLOR, pady=2)
        row_go2rtc.pack(anchor="w")
        self.led_go2rtc = StatusLED(row_go2rtc, size=10, bg_color=CARD_COLOR)
        self.led_go2rtc.pack(side="left", padx=(0, 6), pady=4)
        tk.Label(row_go2rtc, text="Ponte RTSP: ", font=("Segoe UI", 9), fg=TEXT_COLOR, bg=CARD_COLOR).pack(side="left")
        self.lbl_val_go2rtc = tk.Label(row_go2rtc, text="Verificando...", font=("Segoe UI", 9, "bold"), fg=ORANGE_COLOR, bg=CARD_COLOR)
        self.lbl_val_go2rtc.pack(side="left")
        
        # Linha Google Drive
        row_gdrive = tk.Frame(self.card_global, bg=CARD_COLOR, pady=2)
        row_gdrive.pack(anchor="w")
        self.led_gdrive = StatusLED(row_gdrive, size=10, bg_color=CARD_COLOR)
        self.led_gdrive.pack(side="left", padx=(0, 6), pady=4)
        tk.Label(row_gdrive, text="Google Drive G: ", font=("Segoe UI", 9), fg=TEXT_COLOR, bg=CARD_COLOR).pack(side="left")
        self.lbl_val_gdrive = tk.Label(row_gdrive, text="Verificando...", font=("Segoe UI", 9, "bold"), fg=ORANGE_COLOR, bg=CARD_COLOR)
        self.lbl_val_gdrive.pack(side="left")
        
        # Card 2: Endereço IP e Links de Acesso
        self.card_network = tk.Frame(status_main_frame, bg=CARD_COLOR, bd=1, relief="flat", padx=15, pady=10)
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

        # Card 3: Câmera 1 (Farmácia)
        self.card_c1 = tk.Frame(status_main_frame, bg=CARD_COLOR, bd=1, relief="flat", padx=15, pady=10)
        self.card_c1.grid(row=1, column=0, padx=4, pady=4, sticky="nsew")
        tk.Label(self.card_c1, text="CÂMERA 1 - FARMÁCIA", font=("Segoe UI", 9, "bold"), fg=ACCENT_COLOR, bg=CARD_COLOR).pack(anchor="w")
        
        row_c1_sinal = tk.Frame(self.card_c1, bg=CARD_COLOR, pady=2)
        row_c1_sinal.pack(anchor="w")
        self.led_c1_sinal = StatusLED(row_c1_sinal, size=10, bg_color=CARD_COLOR)
        self.led_c1_sinal.pack(side="left", padx=(0, 6), pady=4)
        tk.Label(row_c1_sinal, text="Sinal: ", font=("Segoe UI", 9), fg=TEXT_COLOR, bg=CARD_COLOR).pack(side="left")
        self.lbl_val_c1_sinal = tk.Label(row_c1_sinal, text="Verificando...", font=("Segoe UI", 9, "bold"), fg=ORANGE_COLOR, bg=CARD_COLOR)
        self.lbl_val_c1_sinal.pack(side="left")
        
        row_c1_grav = tk.Frame(self.card_c1, bg=CARD_COLOR, pady=2)
        row_c1_grav.pack(anchor="w")
        self.led_c1_grav = StatusLED(row_c1_grav, size=10, bg_color=CARD_COLOR)
        self.led_c1_grav.pack(side="left", padx=(0, 6), pady=4)
        tk.Label(row_c1_grav, text="Gravação: ", font=("Segoe UI", 9), fg=TEXT_COLOR, bg=CARD_COLOR).pack(side="left")
        self.lbl_val_c1_grav = tk.Label(row_c1_grav, text="Verificando...", font=("Segoe UI", 9, "bold"), fg=ORANGE_COLOR, bg=CARD_COLOR)
        self.lbl_val_c1_grav.pack(side="left")

        # Card 4: Câmera 2 (Farmácia)
        self.card_c2 = tk.Frame(status_main_frame, bg=CARD_COLOR, bd=1, relief="flat", padx=15, pady=10)
        self.card_c2.grid(row=1, column=1, padx=4, pady=4, sticky="nsew")
        tk.Label(self.card_c2, text="CÂMERA 2 - FARMÁCIA", font=("Segoe UI", 9, "bold"), fg=ACCENT_COLOR, bg=CARD_COLOR).pack(anchor="w")
        
        row_c2_sinal = tk.Frame(self.card_c2, bg=CARD_COLOR, pady=2)
        row_c2_sinal.pack(anchor="w")
        self.led_c2_sinal = StatusLED(row_c2_sinal, size=10, bg_color=CARD_COLOR)
        self.led_c2_sinal.pack(side="left", padx=(0, 6), pady=4)
        tk.Label(row_c2_sinal, text="Sinal: ", font=("Segoe UI", 9), fg=TEXT_COLOR, bg=CARD_COLOR).pack(side="left")
        self.lbl_val_c2_sinal = tk.Label(row_c2_sinal, text="Verificando...", font=("Segoe UI", 9, "bold"), fg=ORANGE_COLOR, bg=CARD_COLOR)
        self.lbl_val_c2_sinal.pack(side="left")
        
        row_c2_grav = tk.Frame(self.card_c2, bg=CARD_COLOR, pady=2)
        row_c2_grav.pack(anchor="w")
        self.led_c2_grav = StatusLED(row_c2_grav, size=10, bg_color=CARD_COLOR)
        self.led_c2_grav.pack(side="left", padx=(0, 6), pady=4)
        tk.Label(row_c2_grav, text="Gravação: ", font=("Segoe UI", 9), fg=TEXT_COLOR, bg=CARD_COLOR).pack(side="left")
        self.lbl_val_c2_grav = tk.Label(row_c2_grav, text="Verificando...", font=("Segoe UI", 9, "bold"), fg=ORANGE_COLOR, bg=CARD_COLOR)
        self.lbl_val_c2_grav.pack(side="left")

        # 3. CARD DE GRAVAÇÕES (ÚLTIMOS VÍDEOS SALVOS)
        info_frame = tk.Frame(self.root, bg=CARD_COLOR, padx=15, pady=10)
        info_frame.pack(fill="x", padx=20, pady=4)
        
        # Coluna Câmera 1
        self.lbl_title_c1 = tk.Label(info_frame, text="Última Sincronização Câmera 1:", font=("Segoe UI", 8, "bold"), fg=TEXT_MUTED, bg=CARD_COLOR)
        self.lbl_title_c1.pack(anchor="w")
        self.lbl_last_file_c1 = tk.Label(info_frame, text="Buscando arquivos...", font=("Segoe UI", 9, "bold"), fg=TEXT_COLOR, bg=CARD_COLOR, wraplength=580, justify="left")
        self.lbl_last_file_c1.pack(anchor="w", pady=(0, 4))
        
        # Divisor de linha discreto
        line = tk.Frame(info_frame, height=1, bg="#2D2D2D", pady=2)
        line.pack(fill="x", pady=2)
        
        # Coluna Câmera 2
        self.lbl_title_c2 = tk.Label(info_frame, text="Última Sincronização Câmera 2:", font=("Segoe UI", 8, "bold"), fg=TEXT_MUTED, bg=CARD_COLOR)
        self.lbl_title_c2.pack(anchor="w")
        self.lbl_last_file_c2 = tk.Label(info_frame, text="Buscando arquivos...", font=("Segoe UI", 9, "bold"), fg=TEXT_COLOR, bg=CARD_COLOR, wraplength=580, justify="left")
        self.lbl_last_file_c2.pack(anchor="w")

        # 4. CONTROLES / BOTÕES
        btn_frame = tk.Frame(self.root, bg=BG_COLOR, pady=10)
        btn_frame.pack(fill="x", padx=20)
        
        self.btn_start = tk.Button(
            btn_frame, 
            text=" ▶️ Iniciar Gravação Dupla", 
            font=("Segoe UI", 11, "bold"), 
            fg="#FFFFFF", 
            bg="#059669", 
            activebackground="#047857", 
            activeforeground="#FFFFFF",
            bd=0, 
            cursor="hand2",
            padx=15, 
            pady=8,
            command=self.click_iniciar
        )
        self.btn_start.pack(side="left", padx=4, expand=True, fill="x")
        
        self.btn_stop = tk.Button(
            btn_frame, 
            text=" ⏹️ Parar Gravação Dupla", 
            font=("Segoe UI", 11, "bold"), 
            fg="#FFFFFF", 
            bg="#DC2626", 
            activebackground="#B91C1C", 
            activeforeground="#FFFFFF",
            bd=0, 
            cursor="hand2",
            padx=15, 
            pady=8,
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
            pady=6,
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
            pady=6,
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
            pady=6,
            command=self.click_monitor
        )
        self.btn_monitor.pack(side="left", padx=4, expand=True, fill="x")


        # 5. LOG DE EVENTOS (CONSOLE)
        log_title_frame = tk.Frame(self.root, bg=BG_COLOR)
        log_title_frame.pack(fill="x", padx=25, pady=(8,0))
        tk.Label(log_title_frame, text="Log de Eventos:", font=("Segoe UI", 8, "bold"), fg=TEXT_MUTED, bg=BG_COLOR).pack(anchor="w")
        
        self.txt_log = tk.Text(self.root, height=4, bg="#030712", fg="#34D399", font=("Consolas", 9), bd=0, padx=10, pady=5)
        self.txt_log.pack(fill="x", padx=20, pady=(2, 8))
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
            # 1. Verifica go2rtc
            go2rtc_ok = self.check_process_go2rtc()
            
            # 2. Verifica se os gravadores 1 e 2 estão rodando
            c1_grav_ok = self.check_process_recorder("gravando_c1.lock")
            c2_grav_ok = self.check_process_recorder("gravando_c2.lock")
            
            # 3. Verifica Google Drive
            gdrive_ok = os.path.exists(GDRIVE_ROOT)
            
            # 4. Verifica sinais das câmeras na API do go2rtc
            c1_signal_str = self.check_rtsp_stream(go2rtc_ok, "farmacia")
            c2_signal_str = self.check_rtsp_stream(go2rtc_ok, "farmacia2")
            
            # 5. Obtém última gravação de cada uma
            last_file_c1 = self.check_last_recording(gdrive_ok, GDRIVE_DIR1)
            last_file_c2 = self.check_last_recording(gdrive_ok, GDRIVE_DIR2)
            
            # Atualiza a interface
            self.root.after(0, self.update_ui_states, 
                            go2rtc_ok, gdrive_ok, 
                            c1_grav_ok, c2_grav_ok, 
                            c1_signal_str, c2_signal_str, 
                            last_file_c1, last_file_c2)
            
            # Dorme por 3 segundos
            time.sleep(3)

    def check_process_go2rtc(self):
        try:
            output = subprocess.check_output('tasklist /FI "IMAGENAME eq go2rtc.exe"', shell=True, text=True)
            return "go2rtc.exe" in output
        except Exception:
            return False

    def check_process_recorder(self, lock_filename):
        try:
            # Verifica processos que contêm gravador_camera.py e o nome do arquivo lock específico
            output = subprocess.check_output(
                f'wmic process where "CommandLine like \'%gravador_camera.py%\' and CommandLine like \'%{lock_filename}%\' and not CommandLine like \'%wmic%\'" get ProcessId',
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
            
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            s.connect(('127.0.0.1', 1984))
            s.close()
        except Exception:
            return "Ponte offline"
            
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
            return "Erro API"

    def check_last_recording(self, gdrive_ok, path):
        if not gdrive_ok or not os.path.exists(path):
            return "Pasta inacessível ou vazia."
            
        try:
            files = [os.path.join(path, f) for f in os.listdir(path) if f.endswith(".mp4")]
            if not files:
                return "Nenhum vídeo salvo encontrado."
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
            return f"{filename} (Sincronizado: {tempo} às {mtime_dt.strftime('%H:%M:%S')})"
        except Exception:
            return "Erro ao ler pasta do Drive"

    # ================= ATUALIZAÇÃO DA GUI =================
    def update_ui_states(self, go2rtc_ok, gdrive_ok, c1_grav_ok, c2_grav_ok, c1_signal, c2_signal, file_c1, file_c2):
        with self.status_lock:
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
                
            # 3. Câmera 1 (Sinal / Gravação)
            if "Sinal OK" in c1_signal:
                self.lbl_val_c1_sinal.configure(text="SINAL OK", fg=GREEN_COLOR)
                self.led_c1_sinal.set_status(GREEN_COLOR, "#065F46")
            elif "Conectando" in c1_signal:
                self.lbl_val_c1_sinal.configure(text="CONECTANDO...", fg=ORANGE_COLOR)
                self.led_c1_sinal.set_status(ORANGE_COLOR, "#78350F")
            else:
                self.lbl_val_c1_sinal.configure(text="SEM SINAL", fg=RED_COLOR)
                self.led_c1_sinal.set_status(RED_COLOR, "#991B1B")
                
            if c1_grav_ok:
                self.lbl_val_c1_grav.configure(text="GRAVANDO", fg=GREEN_COLOR)
                self.led_c1_grav.set_status(GREEN_COLOR, "#065F46")
            else:
                self.lbl_val_c1_grav.configure(text="PARADO", fg=RED_COLOR)
                self.led_c1_grav.set_status(RED_COLOR, "#991B1B")
                
            # 4. Câmera 2 (Sinal / Gravação)
            if "Sinal OK" in c2_signal:
                self.lbl_val_c2_sinal.configure(text="SINAL OK", fg=GREEN_COLOR)
                self.led_c2_sinal.set_status(GREEN_COLOR, "#065F46")
            elif "Conectando" in c2_signal:
                self.lbl_val_c2_sinal.configure(text="CONECTANDO...", fg=ORANGE_COLOR)
                self.led_c2_sinal.set_status(ORANGE_COLOR, "#78350F")
            else:
                self.lbl_val_c2_sinal.configure(text="SEM SINAL", fg=RED_COLOR)
                self.led_c2_sinal.set_status(RED_COLOR, "#991B1B")
                
            if c2_grav_ok:
                self.lbl_val_c2_grav.configure(text="GRAVANDO", fg=GREEN_COLOR)
                self.led_c2_grav.set_status(GREEN_COLOR, "#065F46")
            else:
                self.lbl_val_c2_grav.configure(text="PARADO", fg=RED_COLOR)
                self.led_c2_grav.set_status(RED_COLOR, "#991B1B")
                
            # 5. Detalhes de últimos arquivos
            self.lbl_last_file_c1.configure(text=file_c1)
            self.lbl_last_file_c2.configure(text=file_c2)

    # ================= CLIQUES DE BOTÕES =================
    def click_iniciar(self):
        self.add_log("Iniciando gravação dupla das câmeras...")
        threading.Thread(target=self.run_start_sequence, daemon=True).start()

    def run_start_sequence(self):
        # Encerra qualquer processo anterior
        self.run_stop_sequence()
        time.sleep(1.5)
        
        try:
            # 1. Liga a ponte RTSP go2rtc.exe
            self.add_log("Ligando Ponte RTSP (go2rtc.exe)...")
            subprocess.Popen(
                [GO2RTC_EXE],
                cwd=os.path.dirname(GO2RTC_EXE),
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            time.sleep(2.5)
            
            # Localiza pythonw.exe
            pythonw_path = sys.executable.replace("python.exe", "pythonw.exe")
            if not os.path.exists(pythonw_path):
                pythonw_path = "pythonw"
                
            # 2. Liga gravador da Câmera 1 (Farmácia)
            self.add_log("Iniciando gravador da CÂMERA 1...")
            subprocess.Popen(
                [pythonw_path, RECORDER_SCRIPT, 
                 "--stream", "farmacia", 
                 "--dir", GDRIVE_DIR1, 
                 "--lock", "gravando_c1.lock", 
                 "--log", "c1_erros.log"],
                cwd=PROJ_DIR,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            # 3. Liga gravador da Câmera 2 (Farmácia 2)
            self.add_log("Iniciando gravador da CÂMERA 2...")
            subprocess.Popen(
                [pythonw_path, RECORDER_SCRIPT, 
                 "--stream", "farmacia2", 
                 "--dir", GDRIVE_DIR2, 
                 "--lock", "gravando_c2.lock", 
                 "--log", "c2_erros.log"],
                cwd=PROJ_DIR,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            self.root.after(0, lambda: self.add_log("Inicialização concluída em segundo plano para ambas as câmeras."))
        except Exception as e:
            self.root.after(0, lambda: self.add_log(f"ERRO ao iniciar gravação: {str(e)}"))
            self.root.after(0, lambda: messagebox.showerror("Erro ao Iniciar", f"Não foi possível iniciar o serviço duplo:\n{str(e)}"))

    def click_parar(self):
        self.add_log("Parando gravação e finalizando processos...")
        threading.Thread(target=self.run_stop_sequence_verbose, daemon=True).start()

    def run_stop_sequence(self):
        # 1. Sinaliza parada para Câmera 1 e Câmera 2 removendo seus locks
        for lock_file in [LOCK_FILE1, LOCK_FILE2]:
            if os.path.exists(lock_file):
                try:
                    os.remove(lock_file)
                except Exception:
                    pass
        self.root.after(0, lambda: self.add_log("Enviando sinal de parada aos gravadores..."))
        
        # 2. Aguarda até 3 segundos para que encerrem de forma graciosa
        for _ in range(15):
            if not self.check_process_recorder("gravando_c1.lock") and not self.check_process_recorder("gravando_c2.lock"):
                break
            time.sleep(0.2)
            
        # 3. Contingência: Força encerramento de qualquer processo do gravador que tenha travado
        try:
            output = subprocess.check_output(
                'wmic process where "CommandLine like \'%gravador_camera.py%\' and not CommandLine like \'%wmic%\'" get ProcessId',
                shell=True,
                text=True,
                stderr=subprocess.DEVNULL
            )
            pids = [line.strip() for line in output.split('\n') if line.strip().isdigit()]
            for pid in pids:
                subprocess.run(f'taskkill /F /PID {pid}', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

        # 4. Encerra go2rtc.exe
        subprocess.run('taskkill /F /IM go2rtc.exe', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def run_stop_sequence_verbose(self):
        self.run_stop_sequence()
        self.root.after(0, lambda: self.add_log("Gravação interrompida. Todos os serviços parados!"))

    def click_abrir_pasta(self):
        if os.path.exists(GDRIVE_ROOT):
            self.add_log("Abrindo pasta de câmeras do Google Drive...")
            os.startfile(GDRIVE_ROOT)
        else:
            self.add_log("ERRO: Pasta G:\\Meu Drive\\CAMERAS inacessível.")
            messagebox.showerror("Erro de Acesso", "Não foi possível abrir o Google Drive. Verifique se ele está rodando.")

    def click_monitor(self):
        html_path = os.path.join(PROJ_DIR, "visualizador.html")
        if os.path.exists(html_path):
            self.add_log("Abrindo Monitor Lado a Lado no navegador...")
            os.startfile(html_path)
        else:
            self.add_log("ERRO: visualizador.html não encontrado!")
            messagebox.showerror("Erro de Acesso", "O arquivo visualizador.html não foi encontrado na pasta do projeto.")


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
            "Script de Gravação": RECORDER_SCRIPT,
            "Script Gerenciador": os.path.join(PROJ_DIR, "gerenciador.pyw")
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
            # Teste Câmera 1
            if os.path.exists(GDRIVE_DIR1):
                log.append(f" - Pasta Câmera 1: Encontrada ({GDRIVE_DIR1})")
                test_file = os.path.join(GDRIVE_DIR1, "teste_diagnostico.tmp")
                try:
                    with open(test_file, "w") as f:
                        f.write("teste")
                    os.remove(test_file)
                    log.append("   [+] Teste de Escrita Câmera 1: SUCESSO")
                except Exception as e:
                    log.append(f"   [-] ERRO de escrita Câmera 1: {str(e)}")
            else:
                log.append(f" - ERRO: Pasta da Câmera 1 NÃO encontrada: {GDRIVE_DIR1}")
                
            # Teste Câmera 2
            if os.path.exists(GDRIVE_DIR2):
                log.append(f" - Pasta Câmera 2: Encontrada ({GDRIVE_DIR2})")
                test_file = os.path.join(GDRIVE_DIR2, "teste_diagnostico.tmp")
                try:
                    with open(test_file, "w") as f:
                        f.write("teste")
                    os.remove(test_file)
                    log.append("   [+] Teste de Escrita Câmera 2: SUCESSO")
                except Exception as e:
                    log.append(f"   [-] ERRO de escrita Câmera 2: {str(e)}")
            else:
                log.append(f" - ERRO: Pasta da Câmera 2 NÃO encontrada: {GDRIVE_DIR2}")
        else:
            log.append(f" - ERRO: Diretório Raiz G:\\Meu Drive\\CAMERAS não foi encontrado!")

        # 4. Processos em Execução
        log.append("\n--- [4] PROCESSOS EM EXECUÇÃO ---")
        go2rtc_running = self.check_process_go2rtc()
        c1_running = self.check_process_recorder("gravando_c1.lock")
        c2_running = self.check_process_recorder("gravando_c2.lock")
        log.append(f" - Processo go2rtc.exe: {'RODANDO' if go2rtc_running else 'PARADO'}")
        log.append(f" - Gravador Câmera 1: {'RODANDO' if c1_running else 'PARADO'}")
        log.append(f" - Gravador Câmera 2: {'RODANDO' if c2_running else 'PARADO'}")

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
        try:
            import cv2
            log.append(f" - OpenCV (cv2) instalado: Versão {cv2.__version__}")
        except ImportError:
            log.append(" - ERRO: OpenCV (cv2) NÃO está instalado!")
        try:
            import numpy
            log.append(f" - NumPy instalado: Versão {numpy.__version__}")
        except ImportError:
            log.append(" - ERRO: NumPy NÃO está instalado!")

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
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
        
    root = tk.Tk()
    app = CameraManagerApp(root)
    root.mainloop()
