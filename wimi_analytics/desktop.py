import queue
import threading
import tkinter as tk
from collections import Counter
from tkinter import messagebox, simpledialog, ttk


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
        "unknown": "Desconhecido",
    }.get(str(value), str(value or "-").replace("_", " ").title())


def _event_text(value):
    return {
        "motion_start": "Movimento iniciado",
        "motion_end": "Movimento encerrado",
        "face_count": "Contagem de rostos",
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


class AnalyticsDesktopWindow:
    REFRESH_MS = 3000

    def __init__(
        self,
        root,
        collector,
        store,
        vision,
        face_service=None,
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
        self._build_reports_tab()
        self._build_people_tab()
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
        for key, title in (("overall", "NVR"), ("hardware", "Hardware"), ("report", "Relatório")):
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
                ("faces", "Rostos"),
                ("identity", "Pessoa"),
                ("updated", "Última amostra"),
            ),
            (130, 100, 110, 120, 80, 170, 160),
            height=13,
        )

    def _build_behavior_tab(self):
        tab = self._tab("Comportamento")
        self._section_title(
            tab,
            "Eventos observáveis",
            "Movimento, presença e duração. O sistema não infere emoção, intenção ou produtividade individual.",
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
            "Coleta agregada de configuração e conectividade. Não captura pacotes, mensagens, senhas ou navegação.",
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
        self._tree(
            tab,
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
            height=14,
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
            "Perfis reconhecíveis",
            "Cadastro local com consentimento explícito. Imagens não são salvas; somente o vetor protegido pelo Windows.",
        )
        actions = tk.Frame(tab, bg=BG)
        actions.pack(fill="x", padx=8, pady=(0, 10))
        self._enroll_button = self._button(
            actions, "Cadastrar rosto", self._enroll_person, GREEN
        )
        self._enroll_button.pack(side="left")
        self._button(actions, "Excluir selecionado", self._delete_person, RED).pack(side="left", padx=8)
        self._labels["face_status"] = tk.Label(
            actions, text="Reconhecimento: verificando", font=("Segoe UI", 9), fg=MUTED, bg=BG
        )
        self._labels["face_status"].pack(side="left", padx=10)
        self._tree(
            tab,
            "people",
            (("name", "Nome"), ("profile", "Identificador local")),
            (320, 560),
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
        self._refresh_overview(payload)
        self._refresh_cameras(payload)
        self._refresh_behavior()
        self._refresh_network(payload)
        self._refresh_reports()
        self._refresh_people()

    def _refresh_overview(self, payload):
        nvr = payload.get("nvr") or {}
        snapshot = nvr.get("snapshot") or {}
        operations = payload.get("operations") or {}
        report = operations.get("report") or {}
        hardware = snapshot.get("hardware_summary") or {}
        values = {
            "overall": _status_text(snapshot.get("overall_status") or nvr.get("state")),
            "hardware": _status_text(hardware.get("smart_status") or "unknown"),
            "report": _status_text(report.get("state") or "waiting_for_data"),
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

    def _refresh_cameras(self, payload):
        vision = self.vision.snapshot()
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

    def _refresh_behavior(self):
        events = self.store.list_vision_events(limit=300)
        counts = Counter(item.get("event_type") for item in events)
        self._labels["behavior_summary"].configure(
            text=(
                f"Movimentos: {counts['motion_start']} | Presenças reconhecidas: "
                f"{counts['presence_confirmed']} | Alterações de rostos: {counts['face_count']}"
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
        self._labels["network_summary"].configure(
            text=(
                f"{_status_text(network.get('state'))} | Conexão: {connection} | Interfaces ativas: "
                f"{connectivity.get('active_interface_count', 0)} | "
                f"{link_details or 'velocidade não informada'} | "
                f"{fault_summary} | "
                f"Cobertura: {network.get('coverage', 'nenhuma')}"
            ),
            fg=(
                GREEN
                if network.get("state") == "active" and not recent_faults and not recent_reset
                else YELLOW
            ),
        )
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
        status = getattr(service, "status", "not_configured") if service else "not_configured"
        self._labels["face_status"].configure(
            text=f"Reconhecimento: {_status_text(status)} | Perfis: {len(profiles)}",
            fg=GREEN if getattr(service, "available", False) else YELLOW,
        )
        self._replace_rows(
            self._trees["people"],
            [(item["profile_id"], (item["display_name"], item["profile_id"])) for item in profiles],
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
        thread = self._enrollment_thread
        if thread and thread is not threading.current_thread():
            thread.join(max(0.1, float(timeout)))
        return not thread or not thread.is_alive()

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
                if action != "enrollment_complete":
                    continue
                with self._enrollment_lock:
                    self._enrollment_busy = False
                if self._destroyed or self.window is None or not self.window.winfo_exists():
                    continue
                if self._enroll_button is not None:
                    self._enroll_button.configure(state="normal")
                if payload:
                    messagebox.showerror("Cadastro não concluído", payload, parent=self.window)
                else:
                    self.refresh()
                    messagebox.showinfo(
                        "Cadastro concluído", "Perfil facial local cadastrado.", parent=self.window
                    )
            finally:
                self._ui_actions.task_done()
        return destroyed

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
        self.face_service.delete_profile(profile_id)
        self.refresh()
        cleanup_error = getattr(getattr(self.face_service, "store", None), "last_cleanup_error", None)
        if cleanup_error:
            messagebox.showwarning(
                "Perfil removido com atenção",
                "O perfil não será mais reconhecido, mas a limpeza física do banco será tentada novamente na manutenção.",
                parent=self.window,
            )
