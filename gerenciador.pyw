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
GDRIVE_DIR = r"G:\Meu Drive\CAMERAS\CAMERA 1 FARMACIA"

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
        # Desenha círculo com borda escura e preenchimento inicial laranja (carregando)
        self.led = self.create_oval(2, 2, size-2, size-2, fill=ORANGE_COLOR, outline="#78350F", width=1)
        
    def set_status(self, color, border_color):
        self.itemconfig(self.led, fill=color, outline=border_color)

class CameraManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Controle da Câmera - Farmácia")
        self.root.geometry("640x630")
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
        
        self.add_log("Painel Premium iniciado. Monitoramento em tempo real ativo.")

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
        header_frame = tk.Frame(self.root, bg=BG_COLOR, pady=15)
        header_frame.pack(fill="x", padx=20)
        
        title_label = tk.Label(
            header_frame, 
            text=" 🎥 Painel Câmera Farmácia", 
            font=("Segoe UI", 18, "bold"), 
            fg=TEXT_COLOR, 
            bg=BG_COLOR
        )
        title_label.pack(side="left")
        
        subtitle_label = tk.Label(
            header_frame, 
            text="v2.1 Premium", 
            font=("Segoe UI", 9, "bold"), 
            fg=ACCENT_COLOR, 
            bg=BG_COLOR
        )
        subtitle_label.pack(side="left", padx=10, pady=8)
        
        # Divisor horizontal elegante
        separator = tk.Frame(self.root, height=1, bg="#1F2937")
        separator.pack(fill="x", padx=20)

        # 2. STATUS GRID (4 CARDS)
        status_main_frame = tk.Frame(self.root, bg=BG_COLOR, pady=12)
        status_main_frame.pack(fill="x", padx=20)
        
        # Ajusta distribuição de largura das duas colunas
        status_main_frame.columnconfigure(0, weight=1, uniform="grid")
        status_main_frame.columnconfigure(1, weight=1, uniform="grid")
        
        # Card 1: Ponte RTSP (go2rtc)
        self.card_go2rtc = tk.Frame(status_main_frame, bg=CARD_COLOR, bd=1, relief="flat", padx=15, pady=10)
        self.card_go2rtc.grid(row=0, column=0, padx=4, pady=4, sticky="nsew")
        tk.Label(self.card_go2rtc, text="Ponte RTSP (go2rtc)", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=CARD_COLOR).pack(anchor="w")
        
        status1_row = tk.Frame(self.card_go2rtc, bg=CARD_COLOR)
        status1_row.pack(anchor="w", pady=4)
        self.led_go2rtc = StatusLED(status1_row, size=12, bg_color=CARD_COLOR)
        self.led_go2rtc.pack(side="left", padx=(0, 6), pady=2)
        self.lbl_val_go2rtc = tk.Label(status1_row, text="Verificando...", font=("Segoe UI", 11, "bold"), fg=ORANGE_COLOR, bg=CARD_COLOR)
        self.lbl_val_go2rtc.pack(side="left")
        
        # Card 2: Gravação de Vídeo (Python)
        self.card_recorder = tk.Frame(status_main_frame, bg=CARD_COLOR, bd=1, relief="flat", padx=15, pady=10)
        self.card_recorder.grid(row=0, column=1, padx=4, pady=4, sticky="nsew")
        tk.Label(self.card_recorder, text="Gravação de Vídeo", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=CARD_COLOR).pack(anchor="w")
        
        status2_row = tk.Frame(self.card_recorder, bg=CARD_COLOR)
        status2_row.pack(anchor="w", pady=4)
        self.led_recorder = StatusLED(status2_row, size=12, bg_color=CARD_COLOR)
        self.led_recorder.pack(side="left", padx=(0, 6), pady=2)
        self.lbl_val_recorder = tk.Label(status2_row, text="Verificando...", font=("Segoe UI", 11, "bold"), fg=ORANGE_COLOR, bg=CARD_COLOR)
        self.lbl_val_recorder.pack(side="left")
        
        # Card 3: Sinal da Câmera
        self.card_camera = tk.Frame(status_main_frame, bg=CARD_COLOR, bd=1, relief="flat", padx=15, pady=10)
        self.card_camera.grid(row=1, column=0, padx=4, pady=4, sticky="nsew")
        tk.Label(self.card_camera, text="Sinal da Câmera", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=CARD_COLOR).pack(anchor="w")
        
        status3_row = tk.Frame(self.card_camera, bg=CARD_COLOR)
        status3_row.pack(anchor="w", pady=4)
        self.led_camera = StatusLED(status3_row, size=12, bg_color=CARD_COLOR)
        self.led_camera.pack(side="left", padx=(0, 6), pady=2)
        self.lbl_val_camera = tk.Label(status3_row, text="Verificando...", font=("Segoe UI", 11, "bold"), fg=ORANGE_COLOR, bg=CARD_COLOR)
        self.lbl_val_camera.pack(side="left")
        
        # Card 4: Google Drive (G:)
        self.card_gdrive = tk.Frame(status_main_frame, bg=CARD_COLOR, bd=1, relief="flat", padx=15, pady=10)
        self.card_gdrive.grid(row=1, column=1, padx=4, pady=4, sticky="nsew")
        tk.Label(self.card_gdrive, text="Google Drive (Pasta G:)", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=CARD_COLOR).pack(anchor="w")
        
        status4_row = tk.Frame(self.card_gdrive, bg=CARD_COLOR)
        status4_row.pack(anchor="w", pady=4)
        self.led_gdrive = StatusLED(status4_row, size=12, bg_color=CARD_COLOR)
        self.led_gdrive.pack(side="left", padx=(0, 6), pady=2)
        self.lbl_val_gdrive = tk.Label(status4_row, text="Verificando...", font=("Segoe UI", 11, "bold"), fg=ORANGE_COLOR, bg=CARD_COLOR)
        self.lbl_val_gdrive.pack(side="left")

        # 3. CARD DE ACESSO NA REDE LOCAL (IP)
        access_frame = tk.Frame(self.root, bg=CARD_COLOR, bd=1, relief="flat", padx=15, pady=10)
        access_frame.pack(fill="x", padx=20, pady=4)
        
        ip_info_frame = tk.Frame(access_frame, bg=CARD_COLOR)
        ip_info_frame.pack(side="left")
        tk.Label(ip_info_frame, text="🌐 Endereço IP deste PC:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=CARD_COLOR).pack(anchor="w")
        tk.Label(ip_info_frame, text=self.local_ip, font=("Segoe UI", 12, "bold"), fg=TEXT_COLOR, bg=CARD_COLOR).pack(anchor="w")
        
        links_info_frame = tk.Frame(access_frame, bg=CARD_COLOR)
        links_info_frame.pack(side="right", anchor="e")
        tk.Label(links_info_frame, text="Acesso em outro PC (clique para copiar):", font=("Segoe UI", 8, "bold"), fg=TEXT_MUTED, bg=CARD_COLOR).pack(anchor="e")
        
        self.lbl_link = tk.Label(
            links_info_frame, 
            text=f"http://{self.local_ip}:1984", 
            font=("Segoe UI", 10, "bold", "underline"), 
            fg=ACCENT_COLOR, 
            cursor="hand2", 
            bg=CARD_COLOR
        )
        self.lbl_link.pack(anchor="e", pady=2)
        self.lbl_link.bind("<Button-1>", lambda e: self.copy_link_to_clipboard())

        # 4. CARD DE INFORMAÇÕES DO DRIVE (ÚLTIMO VÍDEO)
        info_frame = tk.Frame(self.root, bg=CARD_COLOR, padx=15, pady=10)
        info_frame.pack(fill="x", padx=20, pady=4)
        
        tk.Label(info_frame, text="Última Sincronização de Vídeo no Drive:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=CARD_COLOR).pack(anchor="w")
        self.lbl_last_file = tk.Label(info_frame, text="Carregando arquivos...", font=("Segoe UI", 9, "bold"), fg=TEXT_COLOR, bg=CARD_COLOR, wraplength=560, justify="left")
        self.lbl_last_file.pack(anchor="w", pady=2)
        
        self.lbl_last_time = tk.Label(info_frame, text="", font=("Segoe UI", 8, "italic"), fg=TEXT_MUTED, bg=CARD_COLOR)
        self.lbl_last_time.pack(anchor="w")

        # 5. CONTROLES / BOTÕES
        btn_frame = tk.Frame(self.root, bg=BG_COLOR, pady=10)
        btn_frame.pack(fill="x", padx=20)
        
        self.btn_start = tk.Button(
            btn_frame, 
            text=" ▶️ Iniciar Gravação", 
            font=("Segoe UI", 11, "bold"), 
            fg="#FFFFFF", 
            bg="#059669", # Verde Esmeralda escuro
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
            text=" ⏹️ Parar Gravação", 
            font=("Segoe UI", 11, "bold"), 
            fg="#FFFFFF", 
            bg="#DC2626", # Vermelho vibrante escuro
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
            text=" 📁 Abrir Pasta no Drive", 
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

        # 6. LOG DE EVENTOS (CONSOLE)
        log_title_frame = tk.Frame(self.root, bg=BG_COLOR)
        log_title_frame.pack(fill="x", padx=25, pady=(8,0))
        tk.Label(log_title_frame, text="Log de Eventos:", font=("Segoe UI", 8, "bold"), fg=TEXT_MUTED, bg=BG_COLOR).pack(anchor="w")
        
        self.txt_log = tk.Text(self.root, height=4, bg="#030712", fg="#34D399", font=("Consolas", 9), bd=0, padx=10, pady=5)
        self.txt_log.pack(fill="x", padx=20, pady=(2, 10))
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
        self.add_log("Link copiado para a área de transferência!")
        messagebox.showinfo("Copiado", f"O link http://{self.local_ip}:1984 foi copiado com sucesso!")

    # ================= MONITOR LOOP (THREAD SEPARADA) =================
    def monitor_loop(self):
        while self.running_monitor:
            # 1. Verifica go2rtc
            go2rtc_ok = self.check_process_go2rtc()
            
            # 2. Verifica recorder script
            recorder_ok = self.check_process_recorder()
            
            # 3. Verifica Google Drive
            gdrive_ok = os.path.exists(GDRIVE_DIR)
            
            # 4. Verifica camera stream
            camera_status_str = self.check_rtsp_stream(go2rtc_ok)
            
            # 5. Obtém última gravação
            last_file, last_time = self.check_last_recording(gdrive_ok)
            
            # Atualiza a interface
            self.root.after(0, self.update_ui_states, go2rtc_ok, recorder_ok, gdrive_ok, camera_status_str, last_file, last_time)
            
            # Dorme por 3 segundos
            time.sleep(3)

    def check_process_go2rtc(self):
        try:
            output = subprocess.check_output('tasklist /FI "IMAGENAME eq go2rtc.exe"', shell=True, text=True)
            return "go2rtc.exe" in output
        except Exception:
            return False

    def check_process_recorder(self):
        try:
            output = subprocess.check_output(
                'wmic process where "CommandLine like \'%gravador_camera.py%\' and not CommandLine like \'%wmic%\'" get ProcessId',
                shell=True,
                text=True,
                stderr=subprocess.DEVNULL
            )
            pids = [line.strip() for line in output.split('\n') if line.strip().isdigit()]
            return len(pids) > 0
        except Exception:
            return False

    def check_rtsp_stream(self, go2rtc_ok):
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
                if "farmacia" in data:
                    producers = data["farmacia"].get("producers", [])
                    if producers:
                        return "Sinal OK"
                    else:
                        return "Conectando..."
                else:
                    return "Não configurada"
        except Exception:
            return "Erro API"

    def check_last_recording(self, gdrive_ok):
        if not gdrive_ok:
            return "Pasta do Google Drive Inacessível", ""
            
        try:
            files = [os.path.join(GDRIVE_DIR, f) for f in os.listdir(GDRIVE_DIR) if f.endswith(".mp4")]
            if not files:
                return "Nenhum vídeo salvo encontrado.", ""
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
            return filename, f"Sincronizado: {tempo} às {mtime_dt.strftime('%H:%M:%S')}"
        except Exception:
            return "Erro ao ler pasta do Drive", ""

    # ================= ATUALIZAÇÃO DA GUI (COM LEDs) =================
    def update_ui_states(self, go2rtc_ok, recorder_ok, gdrive_ok, camera_str, last_file, last_time):
        with self.status_lock:
            # 1. go2rtc status
            if go2rtc_ok:
                self.lbl_val_go2rtc.configure(text="ATIVO", fg=GREEN_COLOR)
                self.led_go2rtc.set_status(GREEN_COLOR, "#065F46")
            else:
                self.lbl_val_go2rtc.configure(text="INATIVO", fg=RED_COLOR)
                self.led_go2rtc.set_status(RED_COLOR, "#991B1B")
                
            # 2. recorder status
            if recorder_ok:
                self.lbl_val_recorder.configure(text="GRAVANDO", fg=GREEN_COLOR)
                self.led_recorder.set_status(GREEN_COLOR, "#065F46")
            else:
                self.lbl_val_recorder.configure(text="PARADO", fg=RED_COLOR)
                self.led_recorder.set_status(RED_COLOR, "#991B1B")
                
            # 3. gdrive status
            if gdrive_ok:
                self.lbl_val_gdrive.configure(text="CONECTADO", fg=GREEN_COLOR)
                self.led_gdrive.set_status(GREEN_COLOR, "#065F46")
            else:
                self.lbl_val_gdrive.configure(text="DESCONECTADO", fg=RED_COLOR)
                self.led_gdrive.set_status(RED_COLOR, "#991B1B")
                
            # 4. camera stream status
            if "Sinal OK" in camera_str:
                self.lbl_val_camera.configure(text="SINAL OK", fg=GREEN_COLOR)
                self.led_camera.set_status(GREEN_COLOR, "#065F46")
            elif "Conectando" in camera_str:
                self.lbl_val_camera.configure(text="CONECTANDO...", fg=ORANGE_COLOR)
                self.led_camera.set_status(ORANGE_COLOR, "#78350F")
            else:
                self.lbl_val_camera.configure(text="SEM SINAL", fg=RED_COLOR)
                self.led_camera.set_status(RED_COLOR, "#991B1B")
                
            # 5. last file details
            self.lbl_last_file.configure(text=last_file)
            self.lbl_last_time.configure(text=last_time)

    # ================= CLIQUES DE BOTÕES =================
    def click_iniciar(self):
        self.add_log("Iniciando processos de gravação...")
        threading.Thread(target=self.run_start_sequence, daemon=True).start()

    def run_start_sequence(self):
        self.run_stop_sequence()
        time.sleep(1)
        
        try:
            self.add_log("Ligando Ponte RTSP (go2rtc.exe)...")
            subprocess.Popen(
                [GO2RTC_EXE],
                cwd=os.path.dirname(GO2RTC_EXE),
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            time.sleep(2)
            
            self.add_log("Ligando script gravador de vídeo (pythonw.exe)...")
            pythonw_path = sys.executable.replace("python.exe", "pythonw.exe")
            if not os.path.exists(pythonw_path):
                pythonw_path = "pythonw"
                
            subprocess.Popen(
                [pythonw_path, RECORDER_SCRIPT],
                cwd=PROJ_DIR,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            self.root.after(0, lambda: self.add_log("Inicialização concluída em segundo plano."))
        except Exception as e:
            self.root.after(0, lambda: self.add_log(f"ERRO ao iniciar: {str(e)}"))
            self.root.after(0, lambda: messagebox.showerror("Erro ao Iniciar", f"Não foi possível iniciar o serviço:\n{str(e)}"))

    def click_parar(self):
        self.add_log("Parando gravação e finalizando processos...")
        threading.Thread(target=self.run_stop_sequence_verbose, daemon=True).start()

    def run_stop_sequence(self):
        subprocess.run('taskkill /F /IM go2rtc.exe', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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

    def run_stop_sequence_verbose(self):
        self.run_stop_sequence()
        self.root.after(0, lambda: self.add_log("Gravação interrompida. Processos parados!"))

    def click_abrir_pasta(self):
        if os.path.exists(GDRIVE_DIR):
            self.add_log("Abrindo pasta do Google Drive...")
            os.startfile(GDRIVE_DIR)
        else:
            self.add_log("ERRO: Google Drive inativo ou inacessível.")
            messagebox.showerror("Erro de Acesso", f"Não foi possível acessar a pasta:\n{GDRIVE_DIR}\n\nVerifique se o Google Drive está conectado.")

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
        if os.path.exists(GDRIVE_DIR):
            log.append(f" - Pasta Google Drive: Encontrada ({GDRIVE_DIR})")
            test_file = os.path.join(GDRIVE_DIR, "teste_diagnostico.tmp")
            try:
                with open(test_file, "w") as f:
                    f.write("teste")
                os.remove(test_file)
                log.append(" - Teste de Escrita no Google Drive: SUCESSO (Permissões de escrita confirmadas)")
            except Exception as e:
                log.append(f" - ERRO de escrita no Google Drive: {str(e)}")
        else:
            log.append(f" - ERRO: Pasta do Google Drive NÃO foi encontrada no caminho: {GDRIVE_DIR}")

        # 4. Processos em Execução
        log.append("\n--- [4] PROCESSOS EM EXECUÇÃO ---")
        go2rtc_running = self.check_process_go2rtc()
        recorder_running = self.check_process_recorder()
        log.append(f" - Processo go2rtc.exe: {'RODANDO' if go2rtc_running else 'PARADO'}")
        log.append(f" - Processo gravador_camera.py: {'RODANDO' if recorder_running else 'PARADO'}")

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
