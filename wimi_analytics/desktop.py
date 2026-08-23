import queue
import threading
import time
import tkinter as tk
from collections import Counter
from tkinter import messagebox, simpledialog, ttk

from PIL import Image, ImageTk


BG = "#0B0D12"
SURFACE = "#141821"
SURFACE_ALT = "#1B2230"
BORDER = "#2A3242"
TEXT = "#F3F4F6"
MUTED = "#AAB4C3"
BLUE = "#3B82F6"
GREEN = "#10B981"
YELLOW = "#F59E0B"
RED = "#EF4444"


def _status_text(value):
    return {
        "active": "Ativo",
        "healthy": "Saudável",
        "current": "Atual",
        "limited": "Limitado",
        "partial": "Parcial",
        "warning": "Atenção",
        "paused": "Pausado",
        "stale": "Desatualizado",
        "unavailable": "Indisponível",
        "not_configured": "Não configurado",
        "waiting_for_data": "Aguardando dados",
        "ready": "Pronto",
        "calibrating": "Calibrando",
        "idle": "Sem movimento",
        "quiet": "Baixa",
        "low": "Leve",
        "moderate": "Moderada",
        "high": "Alta",
        "unknown": "Desconhecido",
    }.get(str(value), str(value or "-").replace("_", " ").title())


def _event_text(value):
    return {
        "motion_start": "Movimento iniciado",
        "motion_end": "Movimento encerrado",
        "face_count": "Contagem de rostos",
        "person_count": "Contagem de pessoas",
        "observed_presence_start": "Presença observada",
        "observed_presence_end": "Presença encerrada",
        "presence_confirmed": "Pessoa reconhecida",
        "analysis_error": "Falha temporária de análise",
    }.get(value, value)


def _connection_text(value):
    return {
        "wired": "Cabo",
        "wireless": "Wi-Fi",
        "virtual": "Virtual",
        "unknown": "Não identificado",
    }.get(str(value or "unknown"), "Não identificado")


def _duration_text(seconds):
    seconds = max(0, int(round(float(seconds or 0))))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min"
    return f"{seconds} s"


def _data_rate_text(bytes_per_second):
    if bytes_per_second is None:
        return "aguardando histórico"
    value = max(0.0, float(bytes_per_second))
    if value >= 1024 * 1024:
        text = f"{value / (1024 * 1024):.1f} MB/s"
    elif value >= 1024:
        text = f"{value / 1024:.1f} KB/s"
    else:
        text = f"{value:.0f} B/s"
    return text.replace(".", ",")


def _byte_size_text(byte_count):
    value = max(0.0, float(byte_count or 0))
    if value >= 1024 * 1024:
        text = f"{value / (1024 * 1024):.1f} MB"
    elif value >= 1024:
        text = f"{value / 1024:.1f} KB"
    else:
        text = f"{value:.0f} B"
    return text.replace(".", ",")


class AnalyticsDesktopWindow:
    REFRESH_MS = 3000
    EVIDENCE_PAGE_SIZE = 24
    EVIDENCE_CARD_WIDTH = 252
    EVIDENCE_CARD_HEIGHT = 230
    EVIDENCE_THUMBNAIL_SIZE = (232, 131)

    def __init__(
        self,
        root,
        collector,
        store,
        vision,
        face_service=None,
        evidence_archive=None,
        camera_widgets=None,
        activate_cameras=None,
        parent=None,
    ):
        self.root = root
        self.parent = parent
        self.embedded = parent is not None
        self.collector = collector
        self.store = store
        self.vision = vision
        self.face_service = face_service
        self.evidence_archive = evidence_archive
        self.camera_widgets = camera_widgets if camera_widgets is not None else {}
        self.activate_cameras = activate_cameras
        self.window = None
        self.notebook = None
        self._after_id = None
        self._destroyed = False
        self._trees = {}
        self._labels = {}
        self._report_detail = None
        self._selected_report_payload = {}
        self._ui_actions = queue.Queue(maxsize=16)
        self._enrollment_lock = threading.Lock()
        self._enrollment_busy = False
        self._enrollment_thread = None
        self._enroll_button = None
        self._deletion_lock = threading.Lock()
        self._deletion_busy = False
        self._deletion_thread = None
        self._delete_button = None
        self._evidence_delete_button = None
        self._evidence_select_all_button = None
        self._evidence_clear_selection_button = None
        self._evidence_previous_button = None
        self._evidence_next_button = None
        self._evidence_page_label = None
        self._evidence_tab = None
        self._evidence_gallery_canvas = None
        self._evidence_gallery_frame = None
        self._evidence_canvas_window = None
        self._evidence_cards = {}
        self._evidence_selection_vars = {}
        self._evidence_photo_cache = {}
        self._evidence_selected_ids = set()
        self._evidence_snapshots = []
        self._evidence_snapshot_signature = None
        self._evidence_status_snapshot = {}
        self._evidence_gallery_dirty = True
        self._evidence_page_index = 0
        self._evidence_grid_columns = 0
        self._responsive_labels = []
        self._last_wraplength = None

    def show(self):
        if self._destroyed:
            return False
        if self.window is None or not self.window.winfo_exists():
            self._build()
        elif self.embedded:
            if not self.window.winfo_manager():
                self.window.pack(fill="both", expand=True)
        else:
            self.window.deiconify()
        if not self.embedded:
            self.window.lift()
            self.window.focus_force()
        self._drain_ui_actions()
        self.refresh()
        self._schedule_refresh()
        return True

    def hide(self):
        self._cancel_refresh()
        if self.window is not None and self.window.winfo_exists():
            if self.embedded:
                self.window.pack_forget()
            else:
                self.window.withdraw()

    def destroy(self):
        if threading.current_thread() is not threading.main_thread():
            self.request_destroy()
            return
        self._destroyed = True
        self._cancel_refresh()
        self._evidence_photo_cache.clear()
        self._evidence_cards.clear()
        if self.window is not None and self.window.winfo_exists():
            self.window.destroy()
        self.window = None

    def request_destroy(self):
        if threading.current_thread() is threading.main_thread():
            self.destroy()
            return
        try:
            self._ui_actions.put_nowait(("destroy", None))
        except queue.Full:
            pass

    def _on_window_destroyed(self, event):
        if event.widget is not self.window:
            return
        self._cancel_refresh()
        self._evidence_photo_cache.clear()
        self._evidence_cards.clear()
        self._evidence_gallery_dirty = True
        self.window = None

    def _build(self):
        if self.embedded:
            window = tk.Frame(self.parent, bg=BG)
            window.pack(fill="both", expand=True)
        else:
            window = tk.Toplevel(self.root)
            window.title("WIMI Analytics - Análise local do NVR")
            window.geometry("1180x760")
            window.minsize(980, 650)
            window.configure(bg=BG)
            window.protocol("WM_DELETE_WINDOW", self.hide)
        self.window = window
        window.bind("<Destroy>", self._on_window_destroyed, add="+")

        style = ttk.Style(window)
        style.configure("Wimi.TNotebook", background=BG, borderwidth=0)
        style.configure(
            "Wimi.TNotebook.Tab",
            background=SURFACE,
            foreground=MUTED,
            padding=(14, 9),
            borderwidth=0,
        )
        style.map(
            "Wimi.TNotebook.Tab",
            background=[("selected", SURFACE_ALT)],
            foreground=[("selected", TEXT)],
        )
        style.configure(
            "Wimi.Treeview",
            background=SURFACE,
            fieldbackground=SURFACE,
            foreground=TEXT,
            bordercolor=BORDER,
            rowheight=28,
        )
        style.configure(
            "Wimi.Treeview.Heading",
            background=SURFACE_ALT,
            foreground=TEXT,
            relief="flat",
        )
        style.map("Wimi.Treeview", background=[("selected", "#17406E")])

        header = tk.Frame(window, bg=BG, height=62 if self.embedded else 76)
        header.pack(fill="x", padx=18 if self.embedded else 22, pady=(10 if self.embedded else 18, 6))
        header.pack_propagate(False)
        tk.Label(
            header,
            text="WIMI Analytics",
            font=("Segoe UI", 17 if self.embedded else 20, "bold"),
            fg=TEXT,
            bg=BG,
        ).pack(anchor="w")
        self._labels["header_status"] = tk.Label(
            header,
            text="Iniciando coleta local...",
            font=("Segoe UI", 9),
            fg=MUTED,
            bg=BG,
        )
        self._labels["header_status"].pack(anchor="w", pady=(3, 0))

        self.notebook = ttk.Notebook(window, style="Wimi.TNotebook")
        self.notebook.pack(fill="both", expand=True, padx=14 if self.embedded else 18, pady=(0, 12 if self.embedded else 18))
        self.notebook.enable_traversal()
        self._build_overview_tab()
        self._build_cameras_tab()
        self._build_behavior_tab()
        self._build_network_tab()
        self._build_evidence_tab()
        self._build_reports_tab()
        self._build_people_tab()
        self.notebook.bind(
            "<<NotebookTabChanged>>", self._on_notebook_tab_changed, add="+"
        )
        window.bind("<Configure>", self._on_resize, add="+")

    def _tab(self, title):
        frame = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(frame, text=title)
        return frame

    def _section_title(self, parent, title, subtitle=None):
        area = tk.Frame(parent, bg=BG)
        area.pack(fill="x", padx=8, pady=(16, 10))
        tk.Label(
            area,
            text=title,
            font=("Segoe UI", 14, "bold"),
            fg=TEXT,
            bg=BG,
        ).pack(anchor="w")
        if subtitle:
            subtitle_label = tk.Label(
                area,
                text=subtitle,
                font=("Segoe UI", 9),
                fg=MUTED,
                bg=BG,
                justify="left",
                wraplength=920,
            )
            subtitle_label.pack(anchor="w", pady=(3, 0))
            self._responsive_labels.append(subtitle_label)

    def _on_resize(self, event):
        if event.widget is not self.window:
            return
        wraplength = max(420, min(1180, int(event.width) - 90))
        if wraplength == self._last_wraplength:
            return
        self._last_wraplength = wraplength
        for label in self._responsive_labels:
            if label.winfo_exists():
                label.configure(wraplength=wraplength)

    def _tree(self, parent, key, columns, widths, height=12):
        wrapper = tk.Frame(parent, bg=BG)
        wrapper.pack(fill="both", expand=True, padx=8, pady=(0, 12))
        tree = ttk.Treeview(
            wrapper,
            columns=[item[0] for item in columns],
            show="headings",
            height=height,
            style="Wimi.Treeview",
        )
        for (column, title), width in zip(columns, widths):
            tree.heading(column, text=title)
            tree.column(column, width=width, minwidth=70, stretch=True)
        scrollbar = ttk.Scrollbar(wrapper, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._trees[key] = tree
        return tree

    def _button(self, parent, text, command, color=BLUE):
        return tk.Button(
            parent,
            text=text,
            command=command,
            font=("Segoe UI", 9, "bold"),
            fg="#FFFFFF",
            bg=color,
            activebackground=color,
            activeforeground="#FFFFFF",
            bd=0,
            padx=14,
            pady=7,
            cursor="hand2",
        )

    def _build_overview_tab(self):
        tab = self._tab("Visão geral")
        self._section_title(tab, "Estado operacional", "Resumo persistente do NVR, hardware e módulos locais.")
        summary = tk.Frame(tab, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        summary.pack(fill="x", padx=8, pady=(0, 12))
        for key, title in (
            ("overall", "NVR"),
            ("hardware", "Hardware"),
            ("cameras_summary", "Câmeras"),
            ("events_summary", "Eventos"),
            ("evidence_summary", "Evidências"),
            ("network_overview", "Rede"),
        ):
            area = tk.Frame(summary, bg=SURFACE)
            area.pack(side="left", fill="both", expand=True, padx=16, pady=14)
            tk.Label(area, text=title, font=("Segoe UI", 9), fg=MUTED, bg=SURFACE).pack(anchor="w")
            label = tk.Label(area, text="Aguardando", font=("Segoe UI", 13, "bold"), fg=YELLOW, bg=SURFACE)
            label.pack(anchor="w", pady=(4, 0))
            self._labels[key] = label
        self._tree(
            tab,
            "modules",
            (("module", "Módulo"), ("status", "Estado"), ("detail", "Detalhe")),
            (190, 130, 590),
            height=11,
        )

    def _build_cameras_tab(self):
        tab = self._tab("Câmeras")
        self._section_title(
            tab,
            "Análise por câmera",
            "A visão usa os quadros do preview já aberto; não cria uma segunda conexão com a câmera.",
        )
        actions = tk.Frame(tab, bg=BG)
        actions.pack(fill="x", padx=8, pady=(0, 10))
        self._button(actions, "Ativar previews e análise", self._activate_camera_analysis, GREEN).pack(side="left")
        self._labels["vision_status"] = tk.Label(
            actions, text="Visão: aguardando", font=("Segoe UI", 9), fg=MUTED, bg=BG
        )
        self._labels["vision_status"].pack(side="left", padx=14)
        self._tree(
            tab,
            "cameras",
            (
                ("camera", "Câmera"),
                ("signal", "Sinal"),
                ("analysis", "Análise"),
                ("motion", "Movimento"),
                ("activity", "Variação visual"),
                ("people", "Pessoas"),
                ("dwell", "Permanência"),
                ("faces", "Rostos"),
                ("identity", "Pessoa"),
                ("updated", "Última amostra"),
            ),
            (115, 85, 90, 90, 90, 70, 105, 65, 135, 145),
            height=13,
        )

    def _build_behavior_tab(self):
        tab = self._tab("Comportamento")
        self._section_title(
            tab,
            "Eventos observáveis",
            "Movimento, presença e duração observada. O sistema não infere emoção, intenção ou produtividade individual.",
        )
        self._labels["behavior_summary"] = tk.Label(
            tab,
            text="Nenhum evento registrado.",
            font=("Segoe UI", 10),
            fg=MUTED,
            bg=BG,
            anchor="w",
        )
        self._labels["behavior_summary"].pack(fill="x", padx=8, pady=(0, 10))
        self._tree(
            tab,
            "events",
            (
                ("when", "Data e hora"),
                ("camera", "Câmera"),
                ("event", "Evento"),
                ("detail", "Detalhe"),
            ),
            (170, 140, 190, 460),
            height=15,
        )

    def _build_network_tab(self):
        tab = self._tab("Rede")
        self._section_title(
            tab,
            "Saúde de rede deste computador",
            "Presença da LAN e aplicativos com TCP ativo neste PC. Não captura pacotes, mensagens, senhas, páginas ou destinos remotos.",
        )
        self._labels["network_summary"] = tk.Label(
            tab,
            text="Aguardando primeira coleta...",
            font=("Segoe UI", 11, "bold"),
            fg=YELLOW,
            bg=BG,
            anchor="w",
            justify="left",
        )
        self._labels["network_summary"].pack(fill="x", padx=8, pady=(0, 12))
        network_views = ttk.Notebook(tab, style="Wimi.TNotebook")
        network_views.pack(fill="both", expand=True, padx=8, pady=(0, 12))
        connection_tab = tk.Frame(network_views, bg=BG)
        devices_tab = tk.Frame(network_views, bg=BG)
        applications_tab = tk.Frame(network_views, bg=BG)
        network_views.add(connection_tab, text="Conexão")
        network_views.add(devices_tab, text="Dispositivos LAN")
        network_views.add(applications_tab, text="Aplicativos deste PC")
        self._tree(
            connection_tab,
            "network_sessions",
            (
                ("started", "Início"),
                ("last_seen", "Último sinal"),
                ("connection", "Conexão"),
                ("duration", "Permanência"),
                ("traffic", "Tráfego agregado"),
                ("state", "Estado"),
            ),
            (155, 155, 100, 110, 170, 95),
            height=5,
        )
        self._tree(
            connection_tab,
            "network",
            (
                ("when", "Data e hora"),
                ("state", "Estado"),
                ("connection", "Conexão"),
                ("interfaces", "Interfaces ativas"),
                ("received", "Recepção"),
                ("sent", "Envio"),
                ("errors", "Erros/desc. acum."),
                ("coverage", "Cobertura"),
            ),
            (155, 90, 105, 95, 100, 100, 115, 220),
            height=8,
        )
        self._tree(
            devices_tab,
            "network_devices",
            (
                ("device", "Identificador"),
                ("ipv4", "IP local"),
                ("interface", "Interface"),
                ("first_seen", "Primeiro sinal"),
                ("last_seen", "Último sinal"),
                ("duration", "Permanência observada"),
                ("neighbor_state", "Cache Windows"),
                ("state", "Sessão"),
            ),
            (125, 115, 120, 155, 155, 150, 115, 90),
            height=14,
        )
        self._tree(
            applications_tab,
            "network_applications",
            (
                ("application", "Aplicativo"),
                ("first_seen", "Início"),
                ("last_seen", "Último sinal"),
                ("duration", "Permanência observada"),
                ("connections", "Conexões atuais"),
                ("peak", "Pico"),
                ("state", "Sessão"),
            ),
            (170, 155, 155, 160, 120, 90, 90),
            height=14,
        )

    def _build_evidence_tab(self):
        tab = self._tab("Evidências")
        self._evidence_tab = tab
        self._section_title(
            tab,
            "Capturas de atendimento anonimizadas",
            "Os rostos detectados são descaracterizados antes da gravação. Exclusão automática após 10 dias; nenhuma captura contém nome ou perfil biométrico.",
        )
        actions = tk.Frame(tab, bg=BG)
        actions.pack(fill="x", padx=8, pady=(0, 8))
        self._evidence_select_all_button = self._button(
            actions, "☑ Marcar tudo", self._select_all_evidence, BLUE
        )
        self._evidence_select_all_button.pack(side="left")
        self._evidence_clear_selection_button = self._button(
            actions, "☐ Desmarcar", self._clear_evidence_selection, SURFACE_ALT
        )
        self._evidence_clear_selection_button.pack(side="left", padx=8)
        self._evidence_delete_button = self._button(
            actions, "✕ Excluir (0)", self._delete_evidence, RED
        )
        self._evidence_delete_button.pack(side="left")
        self._evidence_delete_button.configure(
            state="disabled",
            disabledforeground=MUTED,
        )

        self._evidence_next_button = self._button(
            actions, "›", lambda: self._change_evidence_page(1), SURFACE_ALT
        )
        self._evidence_next_button.configure(width=3, padx=4)
        self._evidence_next_button.pack(side="right")
        self._evidence_page_label = tk.Label(
            actions,
            text="Página 1 de 1",
            font=("Segoe UI", 9, "bold"),
            fg=MUTED,
            bg=BG,
        )
        self._evidence_page_label.pack(side="right", padx=10)
        self._evidence_previous_button = self._button(
            actions, "‹", lambda: self._change_evidence_page(-1), SURFACE_ALT
        )
        self._evidence_previous_button.configure(width=3, padx=4)
        self._evidence_previous_button.pack(side="right")

        status_row = tk.Frame(tab, bg=BG)
        status_row.pack(fill="x", padx=8, pady=(0, 8))
        self._labels["evidence_status"] = tk.Label(
            status_row,
            text="Retenção: 10 dias | aguardando capturas",
            font=("Segoe UI", 9),
            fg=MUTED,
            bg=BG,
            justify="left",
            anchor="w",
            wraplength=1100,
        )
        self._labels["evidence_status"].pack(fill="x")
        self._responsive_labels.append(self._labels["evidence_status"])

        gallery_shell = tk.Frame(tab, bg=BG)
        gallery_shell.pack(fill="both", expand=True, padx=8, pady=(0, 12))
        self._evidence_gallery_canvas = tk.Canvas(
            gallery_shell,
            bg=BG,
            highlightthickness=0,
            bd=0,
        )
        gallery_scrollbar = ttk.Scrollbar(
            gallery_shell,
            orient="vertical",
            command=self._evidence_gallery_canvas.yview,
        )
        self._evidence_gallery_canvas.configure(
            yscrollcommand=gallery_scrollbar.set
        )
        self._evidence_gallery_canvas.pack(side="left", fill="both", expand=True)
        gallery_scrollbar.pack(side="right", fill="y")
        self._evidence_gallery_frame = tk.Frame(
            self._evidence_gallery_canvas,
            bg=BG,
        )
        self._evidence_canvas_window = self._evidence_gallery_canvas.create_window(
            (0, 0),
            window=self._evidence_gallery_frame,
            anchor="nw",
        )
        self._evidence_gallery_frame.bind(
            "<Configure>", self._update_evidence_scrollregion, add="+"
        )
        self._evidence_gallery_canvas.bind(
            "<Configure>", self._layout_evidence_cards, add="+"
        )

    def _build_reports_tab(self):
        tab = self._tab("Relatórios")
        self._section_title(tab, "Histórico persistente", "Snapshots são gravados por mudança ou intervalo de segurança.")
        body = tk.PanedWindow(tab, orient="vertical", bg=BG, sashwidth=5, bd=0)
        body.pack(fill="both", expand=True, padx=8, pady=(0, 12))
        top = tk.Frame(body, bg=BG)
        bottom = tk.Frame(body, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        body.add(top, minsize=220)
        body.add(bottom, minsize=150)
        reports = self._tree(
            top,
            "reports",
            (("when", "Coletado em"), ("source", "Fonte"), ("state", "Estado"), ("headline", "Resumo")),
            (170, 170, 110, 500),
            height=8,
        )
        reports.bind("<<TreeviewSelect>>", self._show_selected_report)
        self._report_detail = tk.Text(
            bottom,
            bg=SURFACE,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            wrap="word",
            font=("Consolas", 9),
            padx=12,
            pady=10,
            state="disabled",
        )
        self._report_detail.pack(fill="both", expand=True)

    def _build_people_tab(self):
        tab = self._tab("Pessoas")
        self._section_title(
            tab,
            "Perfis consentidos mais observados",
            "Ranking por visitas e tempo estimado. Não há cadastro automático; imagens não são salvas.",
        )
        actions = tk.Frame(tab, bg=BG)
        actions.pack(fill="x", padx=8, pady=(0, 10))
        self._enroll_button = self._button(
            actions, "Cadastrar rosto", self._enroll_person, GREEN
        )
        self._enroll_button.pack(side="left")
        self._delete_button = self._button(
            actions, "Excluir selecionado", self._delete_person, RED
        )
        self._delete_button.pack(side="left", padx=8)
        self._labels["face_status"] = tk.Label(
            actions, text="Reconhecimento: verificando", font=("Segoe UI", 9), fg=MUTED, bg=BG
        )
        self._labels["face_status"].pack(side="left", padx=10)
        self._tree(
            tab,
            "people",
            (
                ("rank", "Posição"),
                ("name", "Nome"),
                ("visits", "Visitas"),
                ("duration", "Tempo observado"),
                ("last_seen", "Última observação"),
                ("cameras", "Câmeras"),
            ),
            (70, 190, 70, 130, 170, 210),
            height=14,
        )

    def _replace_rows(self, tree, rows):
        for item in tree.get_children():
            tree.delete(item)
        for item_id, values in rows:
            tree.insert("", "end", iid=item_id or None, values=values)

    def refresh(self):
        if self.window is None or not self.window.winfo_exists():
            return
        collector_state = self.collector.snapshot()
        payload = collector_state.get("payload") or {}
        error = collector_state.get("last_error")
        self._labels["header_status"].configure(
            text=(f"Coleta local com atenção: {error}" if error else "Coleta local ativa e persistente"),
            fg=RED if error else GREEN,
        )
        vision_snapshot = self.vision.snapshot()
        events = self.store.list_vision_events(limit=500)
        evidence_snapshots = (
            self.evidence_archive.list_snapshots(limit=200)
            if self.evidence_archive is not None
            else []
        )
        evidence_status = (
            self.evidence_archive.status()
            if self.evidence_archive is not None
            else {}
        )
        self._refresh_overview(
            payload,
            vision=vision_snapshot,
            events=events,
            evidence=evidence_status,
        )
        self._refresh_cameras(payload, vision=vision_snapshot)
        self._refresh_behavior(events=events)
        self._refresh_network(payload)
        self._refresh_evidence(
            snapshots=evidence_snapshots,
            status=evidence_status,
        )
        self._refresh_reports()
        self._refresh_people()

    def _refresh_overview(self, payload, vision=None, events=None, evidence=None):
        nvr = payload.get("nvr") or {}
        snapshot = nvr.get("snapshot") or {}
        hardware = snapshot.get("hardware_summary") or {}
        vision = self.vision.snapshot() if vision is None else vision
        events = self.store.list_vision_events(limit=500) if events is None else events
        evidence = (
            self.evidence_archive.status()
            if evidence is None and self.evidence_archive is not None
            else evidence or {}
        )
        network = payload.get("network") or {}
        active_cameras = sum(1 for item in vision.values() if item.get("state") == "active")
        values = {
            "overall": _status_text(snapshot.get("overall_status") or nvr.get("state")),
            "hardware": _status_text(hardware.get("smart_status") or "unknown"),
            "cameras_summary": f"{active_cameras}/{len(vision)} ativas" if vision else "Aguardando",
            "events_summary": f"{len(events)} recentes",
            "evidence_summary": f"{evidence.get('count', 0)} em 10 dias",
            "network_overview": _connection_text(
                (network.get("connectivity") or {}).get("primary_connection_type")
            ),
        }
        for key, text in values.items():
            color = GREEN if text in {"Ativo", "Atual", "Saudável"} else YELLOW
            self._labels[key].configure(text=text, fg=color)
        rows = []
        for index, module in enumerate(payload.get("modules") or []):
            rows.append(
                (
                    f"module-{index}",
                    (module.get("label", "-"), _status_text(module.get("status")), module.get("detail", "-")),
                )
            )
        self._replace_rows(self._trees["modules"], rows)

    def _refresh_cameras(self, payload, vision=None):
        vision = self.vision.snapshot() if vision is None else vision
        nvr_metrics = ((payload.get("nvr") or {}).get("snapshot") or {}).get("metrics") or {}
        connectivity = nvr_metrics.get("camera_connectivity") or {}
        names = sorted(set(self.camera_widgets) | set(connectivity) | set(vision))
        rows = []
        for index, stream in enumerate(names):
            widget = self.camera_widgets.get(stream)
            vision_item = vision.get(stream) or {}
            signal_value = connectivity.get(stream, "unknown")
            if isinstance(signal_value, dict):
                signal_value = (
                    signal_value.get("status")
                    or signal_value.get("Status")
                    or signal_value.get("state")
                    or "unknown"
                )
            signal = "Online" if getattr(widget, "is_online", False) else _status_text(signal_value)
            identities = vision_item.get("identities") or []
            identity = ", ".join(item.get("display_name", "Pessoa") for item in identities) or "-"
            rows.append(
                (
                    f"camera-{index}",
                    (
                        stream.upper(),
                        signal,
                        _status_text(vision_item.get("state") or "waiting_for_data"),
                        _status_text(vision_item.get("motion") or "unknown"),
                        _status_text(vision_item.get("activity_level") or "unknown"),
                        vision_item.get("person_count")
                        if vision_item.get("person_count") is not None
                        else "-",
                        _duration_text(vision_item.get("presence_duration_seconds")),
                        vision_item.get("face_count") if vision_item.get("face_count") is not None else "-",
                        identity,
                        vision_item.get("last_analyzed_at") or "-",
                    ),
                )
            )
        self._replace_rows(self._trees["cameras"], rows)
        face = self.face_service
        status = getattr(face, "status", "not_configured") if face else "not_configured"
        active_count = sum(1 for value in vision.values() if value.get("state") == "active")
        calibrating_count = sum(
            1 for value in vision.values() if value.get("state") == "calibrating"
        )
        self._labels["vision_status"].configure(
            text=(
                f"Análise ativa: {active_count}/{len(names)} | "
                f"Calibrando: {calibrating_count} | Rostos: {_status_text(status)}"
            ),
            fg=GREEN if active_count and not calibrating_count else YELLOW,
        )

    def _refresh_behavior(self, events=None):
        events = self.store.list_vision_events(limit=500) if events is None else events
        counts = Counter(item.get("event_type") for item in events)
        motion_seconds = sum(
            max(0.0, float(item.get("duration_seconds") or 0.0))
            for item in events
            if item.get("event_type") == "motion_end"
        )
        presence_seconds = sum(
            max(0.0, float(item.get("duration_seconds") or 0.0))
            for item in events
            if item.get("event_type") == "observed_presence_end"
        )
        peak_people = max(
            (
                max(0, int(item.get("count") or 0))
                for item in events
                if item.get("event_type")
                in {"person_count", "observed_presence_start", "observed_presence_end"}
            ),
            default=0,
        )
        recognized_profiles = {
            item.get("profile_id")
            for item in events
            if item.get("event_type") == "presence_confirmed" and item.get("profile_id")
        }
        self._labels["behavior_summary"].configure(
            text=(
                f"Últimos {len(events)} eventos | Sessões concluídas: {counts['observed_presence_end']} "
                f"({_duration_text(presence_seconds)}) | Pico amostrado: {peak_people} pessoa(s) | "
                f"Movimentos: {counts['motion_start']} ({_duration_text(motion_seconds)}) | "
                f"Perfis consentidos: {len(recognized_profiles)}"
            )
        )
        names = {
            item["profile_id"]: item["display_name"]
            for item in (self.face_service.list_profiles() if self.face_service else [])
        }
        rows = []
        for index, event in enumerate(events[:200]):
            detail = ""
            if event.get("event_type") == "face_count":
                detail = f"{event.get('count', 0)} rosto(s)"
            elif event.get("event_type") == "person_count":
                detail = f"{event.get('count', 0)} pessoa(s)"
            elif event.get("event_type") == "observed_presence_start":
                detail = f"{event.get('count', 0)} pessoa(s) no início"
            elif event.get("event_type") == "observed_presence_end":
                detail = (
                    f"pico {event.get('count', 0)} | "
                    f"{_duration_text(event.get('duration_seconds'))}"
                )
            elif event.get("event_type") == "presence_confirmed":
                detail = names.get(event.get("profile_id"), "Perfil local")
            elif event.get("duration_seconds") is not None:
                detail = f"{event['duration_seconds']:.1f} s"
            rows.append(
                (
                    f"event-{index}",
                    (event.get("occurred_at", "-"), event.get("stream", "-").upper(), _event_text(event.get("event_type")), detail),
                )
            )
        self._replace_rows(self._trees["events"], rows)

    def _refresh_network(self, payload):
        network = payload.get("network") or {}
        connectivity = network.get("connectivity") or {}
        connection = _connection_text(connectivity.get("primary_connection_type"))
        link_details = ", ".join(
            " ".join(
                part
                for part in (item.get("alias"), item.get("link_speed"))
                if part
            )
            for item in (network.get("interfaces") or [])[:3]
        )
        samples = self.store.list_network_samples(limit=200)
        sessions = self.store.list_network_sessions(limit=50)
        devices = self.store.list_network_device_sessions(limit=100)
        applications = self.store.list_local_application_sessions(limit=100)
        traffic = self.store.summarize_network_traffic(limit=120, samples=samples[:120])
        recent_faults = samples[0].get("error_delta") if samples else None
        recent_reset = bool(samples and samples[0].get("counter_reset_detected"))
        if recent_reset:
            fault_summary = "contadores reiniciados; variação inconclusiva"
        elif recent_faults is None:
            fault_summary = "comparação aguardando próxima amostra"
        elif recent_faults:
            fault_summary = f"+{recent_faults} erro(s)/descarte(s) desde a amostra anterior"
        else:
            fault_summary = "sem novos erros/descartes na última amostra"
        anomaly_text = {
            "collection_unavailable": "coleta de rede indisponível",
            "continuity_changed": "continuidade dos contadores alterada",
            "counter_reset": "contadores reiniciados; medição inconclusiva",
            "link_errors": "novos erros ou descartes detectados",
            "traffic_spike": "pico de tráfego acima do histórico local",
            "insufficient_history": "histórico ainda insuficiente",
            "none": "sem anomalia agregada",
        }.get(traffic.get("anomaly"), "estado agregado desconhecido")
        active_session = next((item for item in sessions if item.get("active")), None)
        session_text = (
            f"Sessão atual: {_connection_text(active_session.get('connection_type'))} desde "
            f"{active_session.get('started_at')} | permanência medida: "
            f"{_duration_text(active_session.get('duration_seconds'))}"
            if active_session
            else "Nenhuma sessão de rede ativa registrada"
        )
        gateway = network.get("gateway_probe") or {}
        gateway_state = gateway.get("state")
        gateway_latency = gateway.get("latency_ms")
        if gateway_state == "reachable":
            gateway_text = (
                f"Gateway: acessível em {gateway_latency:.0f} ms"
                if isinstance(gateway_latency, (int, float))
                else "Gateway: acessível"
            )
        elif gateway_state == "not_configured":
            gateway_text = "Gateway: não configurado"
        elif gateway_state == "inconclusive":
            gateway_text = "Gateway: teste ICMP inconclusivo"
        else:
            gateway_text = "Gateway: medição indisponível"
        lan_visibility = network.get("lan_visibility") or {}
        application_visibility = network.get("application_visibility") or {}
        if lan_visibility.get("state") == "partial":
            lan_text = (
                f"LAN: {lan_visibility.get('device_count', 0)} dispositivo(s) visto(s); "
                "visão parcial do cache do Windows"
            )
        else:
            lan_text = "LAN: presença de dispositivos indisponível"
        if application_visibility.get("state") == "available":
            application_text = (
                "Aplicativos deste PC com TCP ativo: "
                f"{application_visibility.get('application_count', 0)}"
            )
        else:
            application_text = "Aplicativos deste PC: medição indisponível"
        self._labels["network_summary"].configure(
            text=(
                f"{_status_text(network.get('state'))} | Conexão: {connection} | Interfaces ativas: "
                f"{connectivity.get('active_interface_count', 0)} | "
                f"{link_details or 'velocidade não informada'}\n"
                f"{fault_summary} | Cobertura: {network.get('coverage', 'nenhuma')}\n"
                f"Tráfego deste PC: {_data_rate_text(traffic.get('current_bytes_per_second'))} | "
                f"Referência histórica: {_data_rate_text(traffic.get('baseline_bytes_per_second'))} | "
                f"{anomaly_text}\n"
                f"{gateway_text} | {lan_text}\n"
                f"{application_text}\n"
                f"{session_text}\n"
                "Privacidade: conteúdo não coletado; credenciais, URLs e destinos remotos não são coletados"
            ),
            fg=(
                GREEN
                if network.get("state") == "active"
                and traffic.get("state") in {"active", "idle"}
                and not recent_faults
                and not recent_reset
                else YELLOW
            ),
        )
        session_rows = []
        for item in sessions:
            traffic_bytes = int(item.get("received_bytes") or 0) + int(
                item.get("sent_bytes") or 0
            )
            session_rows.append(
                (
                    str(item.get("id")),
                    (
                        item.get("started_at", "-"),
                        item.get("last_seen_at", "-"),
                        _connection_text(item.get("connection_type")),
                        _duration_text(item.get("duration_seconds")),
                        _byte_size_text(traffic_bytes),
                        "Ativa" if item.get("active") else "Encerrada",
                    ),
                )
            )
        self._replace_rows(self._trees["network_sessions"], session_rows)
        device_rows = []
        for item in devices:
            device_rows.append(
                (
                    f"device-{item.get('id')}",
                    (
                        f"#{str(item.get('device_id') or '')[:8]}",
                        item.get("ipv4", "-"),
                        item.get("interface_alias", "-"),
                        item.get("started_at", "-"),
                        item.get("last_seen_at", "-"),
                        _duration_text(item.get("duration_seconds")),
                        str(item.get("last_state") or "-").capitalize(),
                        (
                            "Vista agora"
                            if item.get("active")
                            and lan_visibility.get("state") == "partial"
                            else "Sem confirmação"
                            if item.get("active")
                            else "Encerrada"
                        ),
                    ),
                )
            )
        self._replace_rows(self._trees["network_devices"], device_rows)
        application_rows = []
        for item in applications:
            application_rows.append(
                (
                    f"application-{item.get('id')}",
                    (
                        item.get("application_name", "-"),
                        item.get("started_at", "-"),
                        item.get("last_seen_at", "-"),
                        _duration_text(item.get("duration_seconds")),
                        item.get("current_connection_count", 0),
                        item.get("peak_connection_count", 0),
                        (
                            "Ativa"
                            if item.get("active")
                            and application_visibility.get("state") == "available"
                            else "Sem confirmação"
                            if item.get("active")
                            else "Encerrada"
                        ),
                    ),
                )
            )
        self._replace_rows(self._trees["network_applications"], application_rows)
        rows = []
        for index, item in enumerate(samples):
            received_rate = item.get("received_bytes_per_second")
            sent_rate = item.get("sent_bytes_per_second")
            errors = sum(
                int(item.get(key) or 0)
                for key in ("received_errors", "sent_errors", "received_discarded", "sent_discarded")
            )
            error_delta = item.get("error_delta")
            if item.get("counter_reset_detected"):
                error_text = f"{errors} | reiniciado"
            elif error_delta is not None:
                error_text = f"{errors} | +{error_delta}"
            else:
                error_text = f"{errors} | aguardando"
            rows.append(
                (
                    f"network-{index}",
                    (
                        item.get("collected_at", "-"),
                        _status_text(item.get("state")),
                        _connection_text(item.get("primary_connection_type")),
                        item.get("active_interface_count", 0),
                        f"{received_rate * 8 / 1000:.1f} kbit/s" if received_rate is not None else "Aguardando",
                        f"{sent_rate * 8 / 1000:.1f} kbit/s" if sent_rate is not None else "Aguardando",
                        error_text,
                        item.get("coverage", "-"),
                    ),
                )
            )
        self._replace_rows(self._trees["network"], rows)

    def _refresh_evidence(self, snapshots=None, status=None):
        archive = self.evidence_archive
        if archive is None:
            snapshots = []
            status = {"state": "unavailable", "retention_days": 10, "total_bytes": 0}
        else:
            snapshots = archive.list_snapshots(limit=200) if snapshots is None else snapshots
            status = archive.status() if status is None else status

        snapshots = list(snapshots or [])
        status = dict(status or {})
        signature = tuple(
            (
                item.get("evidence_id"),
                item.get("captured_at"),
                item.get("expires_at"),
                item.get("stream"),
                item.get("face_count"),
                item.get("byte_count"),
                item.get("anonymization"),
            )
            for item in snapshots
        )
        evidence_ids = {
            item.get("evidence_id") for item in snapshots if item.get("evidence_id")
        }
        self._evidence_selected_ids.intersection_update(evidence_ids)
        self._evidence_snapshots = snapshots
        self._evidence_status_snapshot = status
        page_count = self._evidence_page_count()
        self._evidence_page_index = min(
            self._evidence_page_index,
            max(0, page_count - 1),
        )
        if signature != self._evidence_snapshot_signature:
            self._evidence_snapshot_signature = signature
            self._evidence_gallery_dirty = True
        if self._evidence_tab_is_selected() and self._evidence_gallery_dirty:
            self._render_evidence_gallery()
        else:
            self._sync_evidence_card_selection()
        self._update_evidence_controls()

    def _evidence_tab_is_selected(self):
        if self.notebook is None or self._evidence_tab is None:
            return False
        try:
            return self.notebook.select() == str(self._evidence_tab)
        except tk.TclError:
            return False

    def _on_notebook_tab_changed(self, _event=None):
        if not self._evidence_tab_is_selected() or not self._evidence_gallery_dirty:
            return
        self._render_evidence_gallery()
        self._update_evidence_controls()

    def _evidence_page_count(self):
        count = len(self._evidence_snapshots)
        return max(1, (count + self.EVIDENCE_PAGE_SIZE - 1) // self.EVIDENCE_PAGE_SIZE)

    def _evidence_page_items(self):
        start = self._evidence_page_index * self.EVIDENCE_PAGE_SIZE
        return self._evidence_snapshots[start : start + self.EVIDENCE_PAGE_SIZE]

    @staticmethod
    def _evidence_time_text(value):
        text = str(value or "-").replace("T", " ")
        return text[:19]

    def _render_evidence_gallery(self):
        frame = self._evidence_gallery_frame
        if frame is None or not frame.winfo_exists():
            return
        for child in frame.winfo_children():
            child.destroy()
        self._evidence_cards.clear()
        self._evidence_selection_vars.clear()
        self._evidence_photo_cache.clear()

        page_items = self._evidence_page_items()
        if not page_items:
            empty = tk.Label(
                frame,
                text="Nenhuma captura anonimizada disponível.",
                font=("Segoe UI", 11),
                fg=MUTED,
                bg=BG,
            )
            empty.grid(row=0, column=0, padx=18, pady=40, sticky="w")
        else:
            for item in page_items:
                self._build_evidence_card(item)
        self._layout_evidence_cards()
        self._evidence_gallery_dirty = False
        if self._evidence_gallery_canvas is not None:
            self._evidence_gallery_canvas.yview_moveto(0.0)

    def _build_evidence_card(self, item):
        evidence_id = str(item.get("evidence_id") or "")
        if not evidence_id:
            return
        selected = evidence_id in self._evidence_selected_ids
        card = tk.Frame(
            self._evidence_gallery_frame,
            width=self.EVIDENCE_CARD_WIDTH,
            height=self.EVIDENCE_CARD_HEIGHT,
            bg=SURFACE,
            highlightbackground=BLUE if selected else BORDER,
            highlightcolor=BLUE if selected else BORDER,
            highlightthickness=2 if selected else 1,
        )
        card.grid_propagate(False)
        card.pack_propagate(False)

        variable = tk.BooleanVar(value=selected)
        header = tk.Frame(card, bg=SURFACE)
        header.pack(fill="x", padx=8, pady=(6, 4))
        checkbox = tk.Checkbutton(
            header,
            text=f"Selecionar  |  {str(item.get('stream') or '-').upper()}",
            variable=variable,
            command=lambda item_id=evidence_id: self._set_evidence_selected(
                item_id,
                bool(self._evidence_selection_vars[item_id].get()),
            ),
            font=("Segoe UI", 9, "bold"),
            fg=TEXT,
            bg=SURFACE,
            activeforeground=TEXT,
            activebackground=SURFACE,
            selectcolor=SURFACE_ALT,
            anchor="w",
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )
        checkbox.pack(fill="x")

        image_area = tk.Frame(
            card,
            width=self.EVIDENCE_THUMBNAIL_SIZE[0],
            height=self.EVIDENCE_THUMBNAIL_SIZE[1],
            bg=SURFACE_ALT,
        )
        image_area.pack(padx=8)
        image_area.pack_propagate(False)
        photo = self._load_evidence_thumbnail(evidence_id)
        preview = tk.Label(
            image_area,
            image=photo or "",
            text="" if photo else "Captura indisponível",
            font=("Segoe UI", 9),
            fg=MUTED,
            bg=SURFACE_ALT,
            anchor="center",
            cursor="hand2",
        )
        preview.pack(fill="both", expand=True)
        if photo is not None:
            self._evidence_photo_cache[evidence_id] = photo

        captured = self._evidence_time_text(item.get("captured_at"))
        expires = self._evidence_time_text(item.get("expires_at"))
        faces = max(0, int(item.get("face_count") or 0))
        detail = tk.Label(
            card,
            text=(
                f"{captured}\n"
                f"Análise: {faces} rosto(s) anonimizado(s)  |  "
                f"{_byte_size_text(item.get('byte_count'))}\n"
                f"Exclusão automática: {expires}"
            ),
            font=("Segoe UI", 8),
            fg=MUTED,
            bg=SURFACE,
            justify="left",
            anchor="w",
            wraplength=self.EVIDENCE_THUMBNAIL_SIZE[0],
        )
        detail.pack(fill="x", padx=9, pady=(5, 7))
        for widget in (preview, detail):
            widget.bind(
                "<Button-1>",
                lambda _event, item_id=evidence_id: self._toggle_evidence_selected(
                    item_id
                ),
                add="+",
            )

        self._evidence_cards[evidence_id] = {
            "frame": card,
            "checkbox": checkbox,
            "preview": preview,
        }
        self._evidence_selection_vars[evidence_id] = variable

    def _load_evidence_thumbnail(self, evidence_id):
        if self.evidence_archive is None:
            return None
        try:
            source = self.evidence_archive.read_image(evidence_id)
        except Exception:
            return None
        if source is None:
            return None
        image = source.convert("RGB")
        image.thumbnail(self.EVIDENCE_THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
        thumbnail = Image.new("RGB", self.EVIDENCE_THUMBNAIL_SIZE, SURFACE_ALT)
        offset = (
            (self.EVIDENCE_THUMBNAIL_SIZE[0] - image.width) // 2,
            (self.EVIDENCE_THUMBNAIL_SIZE[1] - image.height) // 2,
        )
        thumbnail.paste(image, offset)
        return ImageTk.PhotoImage(thumbnail, master=self.window)

    def _layout_evidence_cards(self, event=None):
        canvas = self._evidence_gallery_canvas
        frame = self._evidence_gallery_frame
        if canvas is None or frame is None:
            return
        width = int(getattr(event, "width", 0) or canvas.winfo_width() or 1)
        try:
            canvas.itemconfigure(self._evidence_canvas_window, width=max(1, width))
        except tk.TclError:
            return
        columns = max(1, width // (self.EVIDENCE_CARD_WIDTH + 16))
        for column in range(max(columns, self._evidence_grid_columns)):
            frame.grid_columnconfigure(
                column,
                weight=1 if column < columns else 0,
                uniform="evidence-gallery" if column < columns else "",
            )
        for index, item in enumerate(self._evidence_page_items()):
            card_data = self._evidence_cards.get(str(item.get("evidence_id") or ""))
            if card_data:
                card_data["frame"].grid(
                    row=index // columns,
                    column=index % columns,
                    padx=6,
                    pady=6,
                    sticky="n",
                )
        self._evidence_grid_columns = columns
        self._update_evidence_scrollregion()

    def _update_evidence_scrollregion(self, _event=None):
        if self._evidence_gallery_canvas is None:
            return
        bounds = self._evidence_gallery_canvas.bbox("all")
        if bounds:
            self._evidence_gallery_canvas.configure(scrollregion=bounds)

    def _sync_evidence_card_selection(self):
        for evidence_id, card in self._evidence_cards.items():
            selected = evidence_id in self._evidence_selected_ids
            variable = self._evidence_selection_vars.get(evidence_id)
            if variable is not None and bool(variable.get()) != selected:
                variable.set(selected)
            card["frame"].configure(
                highlightbackground=BLUE if selected else BORDER,
                highlightcolor=BLUE if selected else BORDER,
                highlightthickness=2 if selected else 1,
            )

    def _set_evidence_selected(self, evidence_id, selected):
        valid_ids = {
            str(item.get("evidence_id") or "") for item in self._evidence_snapshots
        }
        if evidence_id not in valid_ids:
            return
        if selected:
            self._evidence_selected_ids.add(evidence_id)
        else:
            self._evidence_selected_ids.discard(evidence_id)
        self._sync_evidence_card_selection()
        self._update_evidence_controls()

    def _toggle_evidence_selected(self, evidence_id):
        self._set_evidence_selected(
            evidence_id,
            evidence_id not in self._evidence_selected_ids,
        )

    def _select_all_evidence(self):
        self._evidence_selected_ids = {
            str(item.get("evidence_id") or "")
            for item in self._evidence_snapshots
            if item.get("evidence_id")
        }
        self._sync_evidence_card_selection()
        self._update_evidence_controls()

    def _clear_evidence_selection(self):
        self._evidence_selected_ids.clear()
        self._sync_evidence_card_selection()
        self._update_evidence_controls()

    def _change_evidence_page(self, delta):
        page_count = self._evidence_page_count()
        target = max(0, min(self._evidence_page_index + int(delta), page_count - 1))
        if target == self._evidence_page_index:
            return
        self._evidence_page_index = target
        self._render_evidence_gallery()
        self._update_evidence_controls()

    def _update_evidence_controls(self):
        count = len(self._evidence_snapshots)
        selected_count = len(self._evidence_selected_ids)
        status = self._evidence_status_snapshot
        if self._labels.get("evidence_status") is not None:
            unavailable = self.evidence_archive is None
            text = (
                f"Retenção: {status.get('retention_days', 10)} dias | "
                f"{count} captura(s) | {selected_count} selecionada(s) | "
                f"{_byte_size_text(status.get('total_bytes'))} | "
                "proteção: contexto pixelizado + rostos detectados descaracterizados"
            )
            if unavailable:
                text = "Retenção: 10 dias | arquivo de evidências indisponível"
            self._labels["evidence_status"].configure(
                text=text,
                fg=GREEN if status.get("state") == "active" else YELLOW,
            )

        if self._evidence_delete_button is not None:
            self._evidence_delete_button.configure(
                text=f"✕ Excluir ({selected_count})",
                state="normal" if selected_count else "disabled",
            )
        if self._evidence_select_all_button is not None:
            self._evidence_select_all_button.configure(
                state="normal" if count and selected_count < count else "disabled"
            )
        if self._evidence_clear_selection_button is not None:
            self._evidence_clear_selection_button.configure(
                state="normal" if selected_count else "disabled"
            )

        page_count = self._evidence_page_count()
        start = self._evidence_page_index * self.EVIDENCE_PAGE_SIZE
        shown_from = start + 1 if count else 0
        shown_to = min(count, start + self.EVIDENCE_PAGE_SIZE)
        if self._evidence_page_label is not None:
            self._evidence_page_label.configure(
                text=(
                    f"Página {self._evidence_page_index + 1} de {page_count}"
                    f"  |  {shown_from}-{shown_to} de {count}"
                )
            )
        if self._evidence_previous_button is not None:
            self._evidence_previous_button.configure(
                state="normal" if self._evidence_page_index > 0 else "disabled"
            )
        if self._evidence_next_button is not None:
            self._evidence_next_button.configure(
                state=(
                    "normal"
                    if self._evidence_page_index + 1 < page_count
                    else "disabled"
                )
            )

    def _delete_evidence(self):
        if not self._evidence_selected_ids or self.evidence_archive is None:
            return
        ordered_ids = [
            str(item.get("evidence_id") or "")
            for item in self._evidence_snapshots
            if str(item.get("evidence_id") or "") in self._evidence_selected_ids
        ]
        count = len(ordered_ids)
        prompt = (
            "Excluir definitivamente a captura anonimizada selecionada?"
            if count == 1
            else f"Excluir definitivamente as {count} capturas anonimizadas selecionadas?"
        )
        if not messagebox.askyesno(
            "Excluir capturas",
            prompt,
            parent=self.window,
        ):
            return
        failures = []
        for evidence_id in ordered_ids:
            try:
                deleted = self.evidence_archive.delete(evidence_id)
            except Exception:
                deleted = False
            if not deleted:
                failures.append(evidence_id)
        self._evidence_selected_ids = set(failures)
        self._evidence_snapshot_signature = None
        self._refresh_evidence()
        if failures:
            messagebox.showwarning(
                "Capturas não removidas",
                f"Não foi possível remover {len(failures)} captura(s) agora.",
                parent=self.window,
            )

    def _refresh_reports(self):
        reports = self.store.list_reports(limit=200)
        self._selected_report_payload = {str(item["id"]): item for item in reports}
        rows = []
        for item in reports:
            report = (item.get("payload") or {}).get("report") or {}
            rows.append(
                (
                    str(item["id"]),
                    (
                        item.get("collected_at", "-"),
                        item.get("source_generated_at") or "-",
                        _status_text(item.get("state")),
                        report.get("headline", "-"),
                    ),
                )
            )
        self._replace_rows(self._trees["reports"], rows)

    def _show_selected_report(self, _event=None):
        selection = self._trees["reports"].selection()
        if not selection:
            return
        item = self._selected_report_payload.get(selection[0]) or {}
        payload = item.get("payload") or {}
        report = payload.get("report") or {}
        readiness = payload.get("readiness") or {}
        lines = [report.get("headline", "Relatório sem resumo"), ""]
        for check in report.get("checks") or []:
            lines.append(f"{check.get('label', 'Verificação')}: {_status_text(check.get('status'))} - {check.get('detail', '')}")
        actions = readiness.get("next_actions") or []
        if actions:
            lines.extend(["", "Próximas ações:"] + [f"- {action}" for action in actions])
        self._report_detail.configure(state="normal")
        self._report_detail.delete("1.0", "end")
        self._report_detail.insert("1.0", "\n".join(lines))
        self._report_detail.configure(state="disabled")

    def _refresh_people(self):
        service = self.face_service
        profiles = service.list_profiles() if service else []
        summaries = self.store.list_profile_presence_summary(limit=100)
        summary_by_profile = {item["profile_id"]: item for item in summaries}
        rank_by_profile = {
            item["profile_id"]: index
            for index, item in enumerate(summaries, start=1)
        }
        profiles.sort(
            key=lambda item: (
                rank_by_profile.get(item["profile_id"], 10**9),
                item["display_name"].casefold(),
            )
        )
        status = getattr(service, "status", "not_configured") if service else "not_configured"
        most_observed = ""
        if summaries:
            top_profile = next(
                (
                    item["display_name"]
                    for item in profiles
                    if item["profile_id"] == summaries[0]["profile_id"]
                ),
                "",
            )
            if top_profile:
                visible_name = (
                    top_profile if len(top_profile) <= 32 else f"{top_profile[:29]}..."
                )
                most_observed = f" | Mais observado: {visible_name}"
        self._labels["face_status"].configure(
            text=(
                f"Reconhecimento: {_status_text(status)} | Perfis consentidos: "
                f"{len(profiles)}{most_observed}"
            ),
            fg=GREEN if getattr(service, "available", False) else YELLOW,
        )
        rows = []
        for item in profiles:
            profile_id = item["profile_id"]
            summary = summary_by_profile.get(profile_id) or {}
            rank = rank_by_profile.get(profile_id)
            rows.append(
                (
                    profile_id,
                    (
                        f"{rank}º" if rank else "-",
                        item["display_name"],
                        summary.get("visit_count", 0),
                        _duration_text(summary.get("observed_seconds", 0)),
                        summary.get("last_seen_at") or "Ainda não observado",
                        ", ".join(
                            str(stream).upper() for stream in summary.get("streams", [])
                        )
                        or "-",
                    ),
                )
            )
        self._replace_rows(
            self._trees["people"],
            rows,
        )
        if self._delete_button is not None:
            self._delete_button.configure(
                state="disabled" if self.deletion_running else "normal"
            )

    def _schedule_refresh(self):
        self._cancel_refresh()
        if self.window is not None and self.window.winfo_exists():
            self._after_id = self.window.after(self.REFRESH_MS, self._refresh_tick)

    def _refresh_tick(self):
        self._after_id = None
        try:
            if self._drain_ui_actions():
                return
            self.refresh()
        finally:
            self._schedule_refresh()

    def _cancel_refresh(self):
        if self._after_id and self.window is not None:
            try:
                self.window.after_cancel(self._after_id)
            except tk.TclError:
                pass
        self._after_id = None

    def _activate_camera_analysis(self):
        if self.activate_cameras:
            self.activate_cameras()
        self.refresh()

    def _enroll_person(self):
        service = self.face_service
        if not service or not getattr(service, "available", False):
            messagebox.showwarning(
                "Visão computacional",
                "O mecanismo facial ainda não está disponível. Verifique OpenCV e os modelos locais.",
                parent=self.window,
            )
            return
        stream = simpledialog.askstring(
            "Câmera para cadastro",
            "Digite a câmera com um rosto visível:\n" + ", ".join(sorted(self.camera_widgets)),
            parent=self.window,
        )
        if not stream:
            return
        stream = stream.strip().lower()
        frame = self.vision.get_latest_frame(stream, max_age_seconds=5.0)
        if frame is None:
            messagebox.showwarning(
                "Sem quadro recente",
                "Ative o preview dessa câmera e aguarde uma imagem antes de cadastrar.",
                parent=self.window,
            )
            return
        name = simpledialog.askstring("Nome", "Nome da pessoa:", parent=self.window)
        if not name:
            return
        consent = messagebox.askyesno(
            "Consentimento biométrico",
            "A pessoa autorizou o cadastro facial local? A imagem não será salva; somente o vetor protegido pelo Windows.",
            parent=self.window,
        )
        if not consent:
            return
        if not self._start_enrollment(name, frame):
            messagebox.showinfo(
                "Cadastro em andamento",
                "Aguarde a conclusão do cadastro atual.",
                parent=self.window,
            )

    @property
    def enrollment_running(self):
        with self._enrollment_lock:
            return self._enrollment_busy

    def _start_enrollment(self, name, frame):
        with self._enrollment_lock:
            if self._enrollment_busy:
                return False
            self._enrollment_busy = True
        if self._enroll_button is not None:
            self._enroll_button.configure(state="disabled")
        thread = threading.Thread(
            target=self._enrollment_worker,
            args=(str(name), frame),
            name="wimi-face-enrollment",
            daemon=True,
        )
        self._enrollment_thread = thread
        thread.start()
        return True

    def _enrollment_worker(self, name, frame):
        error = None
        try:
            self.face_service.enroll(name, frame, consent=True)
        except Exception as caught:
            error = str(caught)[:200]
        try:
            self._ui_actions.put_nowait(("enrollment_complete", error))
        except queue.Full:
            with self._enrollment_lock:
                self._enrollment_busy = False

    def wait_for_workers(self, timeout=3.0):
        deadline = time.monotonic() + max(0.1, float(timeout))
        threads = (self._enrollment_thread, self._deletion_thread)
        for thread in threads:
            if thread and thread is not threading.current_thread():
                thread.join(max(0.0, deadline - time.monotonic()))
        return all(not thread or not thread.is_alive() for thread in threads)

    def _drain_ui_actions(self):
        destroyed = False
        while True:
            try:
                action, payload = self._ui_actions.get_nowait()
            except queue.Empty:
                break
            try:
                if action == "destroy":
                    self.destroy()
                    destroyed = True
                    continue
                if action == "enrollment_complete":
                    self._finish_enrollment(payload)
                    continue
                if action == "deletion_complete":
                    self._finish_deletion(payload)
                    continue
            finally:
                self._ui_actions.task_done()
        return destroyed

    def _finish_enrollment(self, error):
        with self._enrollment_lock:
            self._enrollment_busy = False
        if self._destroyed or self.window is None or not self.window.winfo_exists():
            return
        if self._enroll_button is not None:
            self._enroll_button.configure(state="normal")
        if error:
            messagebox.showerror("Cadastro não concluído", error, parent=self.window)
        else:
            self.refresh()
            messagebox.showinfo(
                "Cadastro concluído", "Perfil facial local cadastrado.", parent=self.window
            )

    @property
    def deletion_running(self):
        with self._deletion_lock:
            return self._deletion_busy

    def _start_profile_deletion(self, profile_id):
        with self._deletion_lock:
            if self._deletion_busy:
                return False
            self._deletion_busy = True
        if self._delete_button is not None:
            self._delete_button.configure(state="disabled")
        thread = threading.Thread(
            target=self._profile_deletion_worker,
            args=(str(profile_id),),
            name="wimi-profile-deletion",
            daemon=True,
        )
        self._deletion_thread = thread
        thread.start()
        return True

    def _profile_deletion_worker(self, profile_id):
        result = {"deleted": False, "error": False, "cleanup_error": False}
        try:
            self.store.delete_profile_presence(profile_id)
            result["deleted"] = bool(self.face_service.delete_profile(profile_id))
            result["cleanup_error"] = bool(
                getattr(getattr(self.face_service, "store", None), "last_cleanup_error", None)
                or getattr(self.store, "last_cleanup_error", None)
            )
        except Exception:
            result["error"] = True
        try:
            self._ui_actions.put_nowait(("deletion_complete", result))
        except queue.Full:
            with self._deletion_lock:
                self._deletion_busy = False

    def _finish_deletion(self, result):
        with self._deletion_lock:
            self._deletion_busy = False
        if self._destroyed or self.window is None or not self.window.winfo_exists():
            return
        if self._delete_button is not None:
            self._delete_button.configure(state="normal")
        self.refresh()
        if result.get("error"):
            messagebox.showerror(
                "Exclusão não concluída",
                "Não foi possível concluir a exclusão. O perfil permanece disponível para nova tentativa.",
                parent=self.window,
            )
        elif not result.get("deleted"):
            messagebox.showwarning(
                "Perfil não removido",
                "O perfil não foi encontrado no banco biométrico.",
                parent=self.window,
            )
        elif result.get("cleanup_error"):
            messagebox.showwarning(
                "Perfil removido com atenção",
                "O perfil não será mais reconhecido, mas a limpeza física do banco será tentada novamente na manutenção.",
                parent=self.window,
            )

    def _delete_person(self):
        selection = self._trees["people"].selection()
        if not selection or not self.face_service:
            return
        profile_id = selection[0]
        if not messagebox.askyesno(
            "Excluir perfil",
            "Excluir definitivamente o perfil biométrico selecionado?",
            parent=self.window,
        ):
            return
        if not self._start_profile_deletion(profile_id):
            messagebox.showinfo(
                "Exclusão em andamento",
                "Aguarde a conclusão da exclusão atual.",
                parent=self.window,
            )
