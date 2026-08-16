from datetime import datetime


SCHEMA_VERSION = 1


def _check(check_id, label, status, value, detail, evidence):
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "value": value,
        "detail": detail,
        "evidence": evidence,
    }


def _finding(finding_id, label, detail, evidence):
    return {
        "id": finding_id,
        "label": label,
        "detail": detail,
        "evidence": evidence,
    }


def _camera_check(metrics):
    connectivity = metrics.get("camera_connectivity")
    if not isinstance(connectivity, dict) or not connectivity:
        return _check(
            "cameras",
            "Cameras",
            "unknown",
            "Sem medicao ativa",
            "Nenhuma camera possui evidencia na coleta recebida.",
            "nvr.metrics.camera_connectivity",
        )

    cameras = [value for value in connectivity.values() if isinstance(value, dict)]
    statuses = [str(camera.get("status", "")).upper() for camera in cameras]
    online = sum(status == "ONLINE" for status in statuses)
    recording = sum(camera.get("recording_active") is True for camera in cameras)
    attention = sum(
        status in {"OFFLINE", "SEM_DADOS", "RECONNECTING", "RECONECTANDO"}
        for status in statuses
    )
    if attention:
        status = "warning"
    elif cameras and online == len(cameras):
        status = "active"
    else:
        status = "limited"
    return _check(
        "cameras",
        "Cameras",
        status,
        f"{online}/{len(cameras)} online",
        f"{recording} gravando com evidencia na coleta.",
        "nvr.metrics.camera_connectivity",
    )


def _issue_status(issues):
    severities = {
        str(issue.get("severity", "")).lower()
        for issue in issues
        if isinstance(issue, dict)
    }
    if severities & {"critical", "error"}:
        return "critical"
    if severities & {"warning", "attention"}:
        return "warning"
    return "limited" if severities else "active"


def _alerts_check(snapshot):
    issues = snapshot.get("issues") if isinstance(snapshot, dict) else None
    issues = [issue for issue in issues or [] if isinstance(issue, dict)]
    count = len(issues)
    if not count:
        return _check(
            "alerts",
            "Ocorrencias da coleta",
            "active",
            "0 ocorrencias",
            "Nenhum alerta sanitizado esta ativo nesta coleta.",
            "nvr.issues",
        )
    first_summary = issues[0].get("summary") or issues[0].get("code") or "Revisao necessaria."
    value = "1 ocorrencia" if count == 1 else f"{count} ocorrencias"
    return _check(
        "alerts",
        "Ocorrencias da coleta",
        _issue_status(issues),
        value,
        first_summary,
        "nvr.issues",
    )


def _storage_check(metrics, issues=None):
    available = metrics.get("hd_available")
    free_gb = metrics.get("hd_free_gb")
    storage_issues = [
        issue
        for issue in issues or []
        if isinstance(issue, dict) and str(issue.get("code", "")).upper().startswith("HD_")
    ]
    if available is True:
        value = f"{free_gb:.1f} GB livres" if isinstance(free_gb, (int, float)) else "Disponivel"
        if storage_issues:
            detail = (
                storage_issues[0].get("summary")
                or storage_issues[0].get("code")
                or "O NVR registrou uma ocorrencia no HD principal."
            )
            return _check(
                "storage",
                "HD de gravacao",
                _issue_status(storage_issues),
                value,
                detail,
                "nvr.issues+nvr.metrics.hd_available",
            )
        return _check(
            "storage",
            "HD de gravacao",
            "active",
            value,
            "Destino principal informado como disponivel pelo NVR.",
            "nvr.metrics.hd_available",
        )
    if available is False:
        return _check(
            "storage",
            "HD de gravacao",
            "critical",
            "Indisponivel",
            "O NVR informou que o destino principal nao esta disponivel.",
            "nvr.metrics.hd_available",
        )
    return _check(
        "storage",
        "HD de gravacao",
        "unknown",
        "Sem dado",
        "O snapshot nao contem estado atual do HD.",
        "nvr.metrics.hd_available",
    )


def _recording_check(metrics):
    connectivity = metrics.get("camera_connectivity")
    if not isinstance(connectivity, dict) or not connectivity:
        return _check(
            "recording",
            "Gravacao",
            "unknown",
            "Sem medicao",
            "Nenhuma camera possui estado de gravacao nesta coleta.",
            "nvr.metrics.camera_connectivity.recording_active",
        )
    measured = [
        camera.get("recording_active")
        for camera in connectivity.values()
        if isinstance(camera, dict) and isinstance(camera.get("recording_active"), bool)
    ]
    if not measured:
        return _check(
            "recording",
            "Gravacao",
            "unknown",
            "Sem medicao",
            "O snapshot nao confirma se os gravadores estao ativos.",
            "nvr.metrics.camera_connectivity.recording_active",
        )
    active = sum(value is True for value in measured)
    return _check(
        "recording",
        "Gravacao",
        "active" if active else "limited",
        f"{active}/{len(measured)} gravando" if active else "Parada",
        "Estado das tarefas de gravacao, separado da conectividade das cameras.",
        "nvr.metrics.camera_connectivity.recording_active",
    )


def _backup_check(metrics):
    count = metrics.get("pending_backup_count")
    pending_gb = metrics.get("pending_backup_gb")
    if not isinstance(count, (int, float)) or isinstance(count, bool):
        return _check(
            "backups",
            "Backups pendentes",
            "unknown",
            "Sem dado",
            "A fila de sincronizacao nao foi medida nesta coleta.",
            "nvr.metrics.pending_backup_count",
        )
    status = "active" if count == 0 else "warning"
    detail = "Nenhum bloco aguarda sincronizacao."
    if count:
        size = f" ({pending_gb:.1f} GB)" if isinstance(pending_gb, (int, float)) else ""
        detail = f"Existem blocos aguardando sincronizacao{size}."
    return _check(
        "backups",
        "Backups pendentes",
        status,
        str(int(count)),
        detail,
        "nvr.metrics.pending_backup_count",
    )


def _hardware_check(snapshot):
    hardware = snapshot.get("hardware_summary") if isinstance(snapshot, dict) else None
    intelligence = snapshot.get("intelligence") if isinstance(snapshot, dict) else None
    protection = intelligence.get("hardware_protection") if isinstance(intelligence, dict) else None
    if not isinstance(hardware, dict) and not isinstance(protection, dict):
        return _check(
            "hardware",
            "Protecao de hardware",
            "unknown",
            "Sem dado",
            "Nao ha resumo de hardware sanitizado nesta coleta.",
            "nvr.hardware_summary",
        )

    new_kernel = hardware.get("kernel_144_new_in_session") if isinstance(hardware, dict) else None
    smart_status = hardware.get("smart_status") if isinstance(hardware, dict) else None
    drive_warnings = hardware.get("drive_warning_count") if isinstance(hardware, dict) else None
    maintenance_allowed = (
        protection.get("heavy_maintenance_allowed") if isinstance(protection, dict) else None
    )
    if (
        maintenance_allowed is False
        or (isinstance(new_kernel, (int, float)) and new_kernel > 0)
        or (isinstance(drive_warnings, (int, float)) and drive_warnings > 0)
    ):
        status = "warning"
        value = "Atencao"
    elif str(smart_status).lower() in {"degraded", "critical", "warning", "pred fail"}:
        status = "warning"
        value = "Atencao"
    elif str(smart_status).lower() == "ok" or maintenance_allowed is True:
        status = "active"
        value = "Monitorado"
    else:
        status = "limited"
        value = "Parcial"
    reason = protection.get("reason") if isinstance(protection, dict) else None
    return _check(
        "hardware",
        "Protecao de hardware",
        status,
        value,
        reason or "Resumo basico do Windows disponivel.",
        "nvr.intelligence.hardware_protection",
    )


def _network_check(network):
    state = network.get("state") if isinstance(network, dict) else None
    if state == "active":
        connectivity = network.get("connectivity") or {}
        ready = bool(
            connectivity.get("active_interface_count")
            and connectivity.get("default_gateway_configured")
            and connectivity.get("dns_configured")
        )
        return _check(
            "network",
            "Rede deste PC",
            "limited" if ready else "warning",
            "Configurada" if ready else "Incompleta",
            "A coleta cobre somente este computador, sem trafego da loja.",
            "network.coverage=host_configuration_only",
        )
    status = "not_configured" if state in {"not_configured", "unsupported"} else "unavailable"
    return _check(
        "network",
        "Rede deste PC",
        status,
        "Sem coleta",
        "O diagnostico local de rede nao esta disponivel.",
        "network.state",
    )


def build_operational_report(nvr, network):
    snapshot = nvr.get("snapshot") if isinstance(nvr, dict) else None
    metrics = snapshot.get("metrics") if isinstance(snapshot, dict) else {}
    if not isinstance(metrics, dict):
        metrics = {}
    issues = snapshot.get("issues") if isinstance(snapshot, dict) else []
    if not isinstance(issues, list):
        issues = []
    nvr_state = nvr.get("state") if isinstance(nvr, dict) else "unavailable"
    network_state = network.get("state") if isinstance(network, dict) else None
    if nvr_state == "active" and network_state == "active":
        state = "current"
        headline = "Relatorio operacional atual"
    elif isinstance(snapshot, dict):
        state = "partial"
        headline = "Relatorio com dados possivelmente desatualizados"
    else:
        state = "unavailable"
        headline = "Relatorio aguardando coleta valida do NVR"

    nvr_check = _check(
        "nvr_snapshot",
        "Coleta do NVR",
        nvr_state,
        "Atual" if nvr_state == "active" else "Indisponivel" if snapshot is None else "Desatualizada",
        nvr.get("reason", "snapshot_unavailable") if isinstance(nvr, dict) else "snapshot_unavailable",
        "nvr.state",
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "state": state,
        "scope": "nvr_and_host_only",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_generated_at": snapshot.get("generated_at") if isinstance(snapshot, dict) else None,
        "headline": headline,
        "checks": [
            nvr_check,
            _alerts_check(snapshot),
            _camera_check(metrics),
            _recording_check(metrics),
            _storage_check(metrics, issues),
            _backup_check(metrics),
            _hardware_check(snapshot),
            _network_check(network),
        ],
    }


def build_readiness(nvr, network, modules, report, runtime=None):
    snapshot = nvr.get("snapshot") if isinstance(nvr, dict) else None
    metrics = snapshot.get("metrics") if isinstance(snapshot, dict) else {}
    hardware = snapshot.get("hardware_summary") if isinstance(snapshot, dict) else {}
    intelligence = snapshot.get("intelligence") if isinstance(snapshot, dict) else {}
    protection = intelligence.get("hardware_protection") if isinstance(intelligence, dict) else {}
    snapshot_current = nvr.get("state") == "active"
    issues = snapshot.get("issues") if isinstance(snapshot, dict) else []
    if not isinstance(issues, list):
        issues = []
    current_issues = [issue for issue in issues if isinstance(issue, dict)] if snapshot_current else []
    module_by_id = {
        module.get("id"): module for module in modules if isinstance(module, dict)
    }
    native_mode = (
        isinstance(runtime, dict)
        and isinstance(runtime.get("analytics"), dict)
        and runtime["analytics"].get("mode") == "native"
    )
    strengths = [
        _finding(
            "native_local" if native_mode else "local_read_only",
            "Painel local nativo" if native_mode else "API local e somente leitura",
            (
                "As analises ficam dentro do aplicativo e nao abrem navegador."
                if native_mode
                else "O Analytics escuta no loopback e nao possui rotas de escrita."
            ),
            "service.mode=native_local" if native_mode else "service.mode=local_read_only",
        ),
        _finding(
            "recording_isolated",
            "Gravacao isolada do Analytics",
            "O Analytics nao altera gravacoes nem arquivos de video; a visao reutiliza previews autorizados.",
            "architecture.analytics_read_only",
        ),
    ]
    if snapshot_current:
        strengths.append(
            _finding(
                "current_snapshot",
                "Coleta atual do NVR",
                "O relatorio usa um snapshot dentro da janela de validade.",
                "nvr.state=active",
            )
        )
    has_storage_issue = any(
        str(issue.get("code", "")).upper().startswith("HD_") for issue in current_issues
    )
    if (
        snapshot_current
        and isinstance(metrics, dict)
        and metrics.get("hd_available") is True
        and not has_storage_issue
    ):
        strengths.append(
            _finding(
                "storage_available",
                "HD de gravacao disponivel",
                "O destino principal esta presente na coleta atual.",
                "nvr.metrics.hd_available=true",
            )
        )
    if snapshot_current and isinstance(metrics, dict) and metrics.get("pending_backup_count") == 0:
        strengths.append(
            _finding(
                "backups_clear",
                "Fila de backup vazia",
                "Nenhum bloco aguarda sincronizacao nesta coleta.",
                "nvr.metrics.pending_backup_count=0",
            )
        )
    if (
        snapshot_current
        and isinstance(hardware, dict)
        and hardware.get("kernel_144_new_in_session") == 0
    ):
        strengths.append(
            _finding(
                "no_new_kernel_144",
                "Sem novo Kernel 144 na sessao",
                "A coleta distingue o historico de uma falha nova.",
                "nvr.hardware_summary.kernel_144_new_in_session=0",
            )
        )

    limitations = []
    vision_status = (module_by_id.get("vision") or {}).get("status")
    if vision_status not in {"active", "limited"}:
        limitations.append(_finding(
            "vision_not_configured",
            "Visao computacional nao configurada",
            "Deteccao, tracking e zonas ainda nao produzem eventos validados.",
            "modules.vision=not_configured",
        ))
    elif vision_status == "limited":
        limitations.append(_finding(
            "vision_waiting_for_frames",
            "Visao aguardando quadros",
            "O worker esta pronto, mas precisa de preview ativo para analisar uma camera.",
            "modules.vision=limited",
        ))
    else:
        strengths.append(_finding(
            "vision_local_active",
            "Visao computacional local ativa",
            "Movimento e rostos sao processados localmente sem salvar imagens.",
            "modules.vision=active",
        ))

    computers_status = (module_by_id.get("computers") or {}).get("status")
    if computers_status not in {"active", "limited"}:
        limitations.append(_finding(
            "computers_not_configured",
            "Agente dos computadores nao configurado",
            "O painel nao recebe uso de aplicativos ou sessoes Windows.",
            "modules.computers=not_configured",
        ))
    elif computers_status == "limited":
        limitations.append(_finding(
            "computers_local_only",
            "Monitoramento limitado a este PC",
            "Nenhum agente remoto foi instalado em outros computadores.",
            "modules.computers=limited",
        ))

    history_active = (
        isinstance(runtime, dict)
        and isinstance(runtime.get("history"), dict)
        and runtime["history"].get("status") == "active"
    )
    if history_active:
        strengths.append(_finding(
            "persistent_history",
            "Historico operacional persistente",
            "Relatorios, rede e eventos ficam no banco local com retencao limitada.",
            "runtime.history=active",
        ))
    else:
        limitations.append(_finding(
            "historical_reports_not_configured",
            "Historico operacional nao configurado",
            "Este relatorio representa a coleta atual e nao inventa totais diarios.",
            "report.scope=nvr_and_host_only",
        ))
    if not isinstance(network, dict) or network.get("can_observe_store_traffic") is not True:
        limitations.append(
            _finding(
                "store_network_not_observed",
                "Trafego da loja nao observado",
                "A rede exibida cobre configuracao e contadores agregados somente deste PC.",
                "network.can_observe_store_traffic=false",
            )
        )
    if not isinstance(hardware, dict) or hardware.get("telemetry_level") != "detailed":
        limitations.append(
            _finding(
                "smart_basic_only",
                "SMART detalhado indisponivel",
                "O status basico do Windows nao mede temperatura ou setores realocados.",
                "nvr.hardware_summary.telemetry_level",
            )
        )
    if isinstance(protection, dict) and protection.get("heavy_maintenance_allowed") is False:
        limitations.append(
            _finding(
                "hardware_attention",
                "Manutencao pesada bloqueada",
                protection.get("reason") or "A protecao de hardware solicitou cautela.",
                "nvr.intelligence.hardware_protection",
            )
        )
    if current_issues:
        first_issue = current_issues[0]
        limitations.append(
            _finding(
                "active_nvr_issues",
                "Ocorrencias ativas no NVR",
                first_issue.get("summary")
                or first_issue.get("code")
                or f"{len(current_issues)} ocorrencia(s) exigem revisao.",
                f"nvr.issues.count={len(current_issues)}",
            )
        )
    if nvr.get("state") != "active":
        limitations.append(
            _finding(
                "nvr_snapshot_not_current",
                "Coleta do NVR nao esta atual",
                "Decisoes operacionais devem aguardar um snapshot valido e recente.",
                f"nvr.state={nvr.get('state', 'unavailable')}",
            )
        )

    module_counts = {}
    for module in modules:
        status = module.get("status", "unknown")
        module_counts[status] = module_counts.get(status, 0) + 1
    status = "limited"
    current_issue_status = _issue_status(current_issues)
    if current_issue_status == "critical":
        status = "critical"
    elif nvr.get("state") in {"unavailable", "unknown"} or (
        isinstance(protection, dict) and protection.get("heavy_maintenance_allowed") is False
    ) or current_issue_status == "warning":
        status = "warning"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "module_counts": module_counts,
        "strengths": strengths,
        "limitations": limitations,
        "next_actions": [item["label"] for item in limitations[:5]],
        "report_state": report.get("state", "unavailable"),
    }
