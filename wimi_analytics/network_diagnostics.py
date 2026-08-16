import copy
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .privacy import DpapiProtector


NETWORK_SCHEMA_VERSION = 1
DEFAULT_CACHE_TTL_SECONDS = 5 * 60
FAILURE_CACHE_TTL_SECONDS = 30
COLLECTOR_TIMEOUT_SECONDS = 12
MAX_COLLECTOR_OUTPUT_BYTES = 64 * 1024
MAX_INTERFACES = 16
MAX_ADDRESSES_PER_FIELD = 8
MAX_LAN_DEVICES = 64
MAX_LOCAL_APPLICATIONS = 64
IDENTIFIER_KEY_BYTES = 32
MAX_PROTECTED_KEY_BYTES = 4096

POWERSHELL_NETWORK_COMMAND = r"""
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$adapters = @{}
Get-NetAdapter -ErrorAction Stop |
    Where-Object { $_.Status -eq 'Up' } |
    ForEach-Object { $adapters[[int]$_.ifIndex] = $_ }
$statistics = @{}
Get-NetAdapterStatistics -ErrorAction SilentlyContinue |
    ForEach-Object { $statistics[[string]$_.Name] = $_ }
$items = @(
    Get-CimInstance Win32_NetworkAdapterConfiguration -Filter 'IPEnabled=True' -ErrorAction Stop |
        ForEach-Object {
            $config = $_
            $adapter = $adapters[[int]$config.InterfaceIndex]
            if ($null -ne $adapter) {
                $stats = $statistics[[string]$adapter.Name]
                [pscustomobject]@{
                    alias = $adapter.Name
                    profile = $null
                    status = $adapter.Status
                    link_speed = $adapter.LinkSpeed
                    media_type = [string]$adapter.MediaType
                    physical_media_type = [string]$adapter.PhysicalMediaType
                    hardware_interface = [bool]$adapter.HardwareInterface
                    ipv4 = @($config.IPAddress)
                    gateway = @($config.DefaultIPGateway)
                    dns = @($config.DNSServerSearchOrder)
                    received_bytes = if ($null -ne $stats) { [uint64]$stats.ReceivedBytes } else { 0 }
                    sent_bytes = if ($null -ne $stats) { [uint64]$stats.SentBytes } else { 0 }
                    received_packets = if ($null -ne $stats) { [uint64]($stats.ReceivedUnicastPackets + $stats.ReceivedMulticastPackets + $stats.ReceivedBroadcastPackets) } else { 0 }
                    sent_packets = if ($null -ne $stats) { [uint64]($stats.SentUnicastPackets + $stats.SentMulticastPackets + $stats.SentBroadcastPackets) } else { 0 }
                    received_errors = if ($null -ne $stats) { [uint64]$stats.ReceivedPacketErrors } else { 0 }
                    sent_errors = if ($null -ne $stats) { [uint64]$stats.OutboundPacketErrors } else { 0 }
                    received_discarded = if ($null -ne $stats) { [uint64]$stats.ReceivedDiscardedPackets } else { 0 }
                    sent_discarded = if ($null -ne $stats) { [uint64]$stats.OutboundDiscardedPackets } else { 0 }
                }
            }
        }
)
$gatewayAddress = @(
    $items | ForEach-Object { @($_.gateway) } | Where-Object { $_ } | Select-Object -First 1
)[0]
$gatewayProbe = [ordered]@{ state = 'not_configured'; latency_ms = $null; address = $gatewayAddress }
if ($gatewayAddress) {
    try {
        $reply = Test-Connection -ComputerName $gatewayAddress -Count 1 -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($null -ne $reply) {
            $gatewayProbe.state = 'reachable'
            $gatewayProbe.latency_ms = [double]$reply.ResponseTime
        } else {
            $gatewayProbe.state = 'inconclusive'
        }
    } catch {
        $gatewayProbe.state = 'inconclusive'
    }
}

$neighborState = 'partial'
$neighbors = @()
try {
    $neighbors = @(
        Get-NetNeighbor -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object {
                $_.State -in @('Reachable', 'Delay', 'Probe') -and
                $_.LinkLayerAddress -and
                $_.LinkLayerAddress -notin @('00-00-00-00-00-00', 'FF-FF-FF-FF-FF-FF')
            } |
            Select-Object -First 64 |
            ForEach-Object {
                [pscustomobject]@{
                    ip_address = [string]$_.IPAddress
                    link_layer_address = [string]$_.LinkLayerAddress
                    interface_alias = [string]$_.InterfaceAlias
                    state = [string]$_.State
                }
            }
    )
} catch {
    $neighborState = 'unavailable'
}

$applicationState = 'available'
$applications = @()
try {
    $applicationRows = @(
        Get-NetTCPConnection -State Established -ErrorAction Stop |
            Group-Object OwningProcess |
            Select-Object -First 128 |
            ForEach-Object {
                $processId = [int]$_.Name
                if ($processId -gt 0) {
                    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
                    if ($null -ne $process) {
                        [pscustomobject]@{
                            name = [string]$process.ProcessName
                            connection_count = [int]$_.Count
                        }
                    }
                }
            }
    )
    $applications = @(
        $applicationRows |
            Group-Object name |
            Select-Object -First 64 |
            ForEach-Object {
                [pscustomobject]@{
                    name = [string]$_.Name
                    connection_count = [int](($_.Group | Measure-Object connection_count -Sum).Sum)
                }
            }
    )
} catch {
    $applicationState = 'unavailable'
}

$payload = [pscustomobject]@{
    interfaces = $items
    gateway_probe = $gatewayProbe
    lan_devices = $neighbors
    local_applications = $applications
    lan_visibility_state = $neighborState
    application_visibility_state = $applicationState
}
ConvertTo-Json -Compress -Depth 5 -InputObject $payload
""".strip()


def _safe_text(value, max_length=120):
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split()).strip()
    return normalized[:max_length] or None


def _safe_ip_list(values, ipv4_only=False):
    if not isinstance(values, list):
        values = [values] if values is not None else []
    result = []
    for value in values[:MAX_ADDRESSES_PER_FIELD]:
        try:
            address = ipaddress.ip_address(str(value))
        except ValueError:
            continue
        if ipv4_only and address.version != 4:
            continue
        text = str(address)
        if text not in result:
            result.append(text)
    return result


def _safe_counter(value):
    try:
        return max(0, min(int(value or 0), (1 << 63) - 1))
    except (TypeError, ValueError, OverflowError):
        return 0


def _safe_private_ipv4(value):
    try:
        address = ipaddress.ip_address(str(value))
    except ValueError:
        return None
    if (
        address.version != 4
        or not address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    ):
        return None
    return str(address)


def load_or_create_identifier_key(path, protector=None):
    path = Path(path).resolve()
    protector = protector or DpapiProtector()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        size = path.stat().st_size
        if size <= 0 or size > MAX_PROTECTED_KEY_BYTES:
            raise ValueError("invalid_network_identity_key")
        protected = path.read_bytes()
        key = protector.unprotect(protected)
        if len(key) != IDENTIFIER_KEY_BYTES:
            raise ValueError("invalid_network_identity_key")
        return key

    key = secrets.token_bytes(IDENTIFIER_KEY_BYTES)
    protected = protector.protect(key)
    if not protected or len(protected) > MAX_PROTECTED_KEY_BYTES:
        raise ValueError("invalid_network_identity_key")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        with open(temporary, "xb") as handle:
            handle.write(protected)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return key


def _device_id(identifier_key, link_layer_address, ipv4, interface_alias):
    normalized_mac = "".join(
        character
        for character in str(link_layer_address or "").strip().lower()
        if character in "0123456789abcdef"
    )
    if len(normalized_mac) == 12 and normalized_mac not in {"0" * 12, "f" * 12}:
        raw_identity = f"mac|{normalized_mac}"
    else:
        raw_identity = f"fallback|{ipv4}|{str(interface_alias or '').strip().lower()}"
    return hmac.new(
        identifier_key,
        f"wimi-lan-device-v1|{raw_identity}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:16]


def _connection_type(raw):
    if raw.get("hardware_interface") is False:
        return "virtual"
    evidence = " ".join(
        str(raw.get(key) or "").lower()
        for key in ("alias", "media_type", "physical_media_type")
    )
    if any(token in evidence for token in ("802.11", "wi-fi", "wifi", "wireless", "wlan")):
        return "wireless"
    if any(token in evidence for token in ("802.3", "ethernet", "gigabit", "fast ethernet")):
        return "wired"
    return "unknown"


def _base_result(state, reason):
    return {
        "schema_version": NETWORK_SCHEMA_VERSION,
        "state": state,
        "reason": reason,
        "coverage": "host_configuration_and_counters",
        "can_observe_store_traffic": False,
        "source": "windows_cim_network_configuration",
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "interfaces": [],
        "connectivity": {
            "active_interface_count": 0,
            "primary_connection_type": "unknown",
            "wired_interface_count": 0,
            "wireless_interface_count": 0,
            "virtual_interface_count": 0,
            "default_gateway_configured": False,
            "dns_configured": False,
        },
        "traffic_counters": {
            "received_bytes": 0,
            "sent_bytes": 0,
            "received_packets": 0,
            "sent_packets": 0,
            "received_errors": 0,
            "sent_errors": 0,
            "received_discarded": 0,
            "sent_discarded": 0,
        },
        "gateway_probe": {"state": "unavailable", "latency_ms": None},
        "lan_visibility": {
            "state": "unavailable",
            "method": "windows_neighbor_cache",
            "device_count": 0,
        },
        "lan_devices": [],
        "application_visibility": {
            "state": "unavailable",
            "scope": "this_host_established_tcp_only",
            "application_count": 0,
        },
        "local_applications": [],
        "privacy": {
            "captures_content": False,
            "captures_credentials": False,
            "captures_remote_endpoints": False,
            "stores_raw_mac": False,
        },
    }


class WindowsNetworkDiagnostics:
    """Bounded read-only view of this Windows host network configuration."""

    def __init__(
        self,
        ttl_seconds=DEFAULT_CACHE_TTL_SECONDS,
        runner=subprocess.run,
        platform_name=None,
        clock=time.monotonic,
        identifier_key=None,
    ):
        self.ttl_seconds = max(5, int(ttl_seconds))
        self.runner = runner
        self.platform_name = platform_name or sys.platform
        self.clock = clock
        self.identifier_key = bytes(identifier_key or secrets.token_bytes(IDENTIFIER_KEY_BYTES))
        if len(self.identifier_key) < 16:
            raise ValueError("network_identifier_key_too_short")
        self._lock = threading.Lock()
        self._cached_result = None
        self._cached_at = 0.0
        self._cached_ttl_seconds = self.ttl_seconds

    def read(self):
        now = self.clock()
        with self._lock:
            if (
                self._cached_result is not None
                and now - self._cached_at < self._cached_ttl_seconds
            ):
                return copy.deepcopy(self._cached_result)
            result = self._collect()
            self._cached_result = result
            self._cached_at = now
            self._cached_ttl_seconds = (
                self.ttl_seconds
                if result.get("state") == "active"
                else min(self.ttl_seconds, FAILURE_CACHE_TTL_SECONDS)
            )
            return copy.deepcopy(result)

    def _collect(self):
        if self.platform_name != "win32":
            return _base_result("unsupported", "windows_required")

        command = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            POWERSHELL_NETWORK_COMMAND,
        ]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = self.runner(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=COLLECTOR_TIMEOUT_SECONDS,
                check=False,
                creationflags=creation_flags,
            )
        except subprocess.TimeoutExpired:
            return _base_result("unavailable", "collector_timeout")
        except OSError:
            return _base_result("unavailable", "collector_unavailable")

        if completed.returncode != 0:
            return _base_result("unavailable", "collector_failed")
        if len(completed.stdout) > MAX_COLLECTOR_OUTPUT_BYTES:
            return _base_result("unavailable", "collector_output_too_large")
        try:
            raw_payload = json.loads(completed.stdout.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError):
            return _base_result("unavailable", "collector_invalid_output")

        wrapped_payload = isinstance(raw_payload, dict) and "interfaces" in raw_payload
        if wrapped_payload:
            raw_interfaces = raw_payload.get("interfaces")
        else:
            raw_interfaces = raw_payload
        if isinstance(raw_interfaces, dict):
            raw_interfaces = [raw_interfaces]
        if not isinstance(raw_interfaces, list):
            return _base_result("unavailable", "collector_invalid_output")

        interfaces = []
        for raw in raw_interfaces[:MAX_INTERFACES]:
            if not isinstance(raw, dict):
                continue
            alias = _safe_text(raw.get("alias"))
            status = _safe_text(raw.get("status"), 32)
            if not alias or str(status).lower() != "up":
                continue
            interfaces.append(
                {
                    "alias": alias,
                    "profile": _safe_text(raw.get("profile")),
                    "status": "up",
                    "link_speed": _safe_text(raw.get("link_speed"), 40),
                    "connection_type": _connection_type(raw),
                    "ipv4": _safe_ip_list(raw.get("ipv4"), ipv4_only=True),
                    "gateways": _safe_ip_list(raw.get("gateway")),
                    "dns_servers": _safe_ip_list(raw.get("dns")),
                    "traffic_counters": {
                        "received_bytes": _safe_counter(raw.get("received_bytes")),
                        "sent_bytes": _safe_counter(raw.get("sent_bytes")),
                        "received_packets": _safe_counter(raw.get("received_packets")),
                        "sent_packets": _safe_counter(raw.get("sent_packets")),
                        "received_errors": _safe_counter(raw.get("received_errors")),
                        "sent_errors": _safe_counter(raw.get("sent_errors")),
                        "received_discarded": _safe_counter(raw.get("received_discarded")),
                        "sent_discarded": _safe_counter(raw.get("sent_discarded")),
                    },
                }
            )

        if not interfaces:
            return _base_result("unavailable", "no_active_interface")

        result = _base_result("active", "host_network_detected")
        if wrapped_payload:
            result["coverage"] = "host_configuration_counters_and_presence"
        result["interfaces"] = interfaces
        primary = next(
            (item for item in interfaces if item["gateways"]),
            interfaces[0],
        )
        result["connectivity"] = {
            "active_interface_count": len(interfaces),
            "primary_connection_type": primary["connection_type"],
            "wired_interface_count": sum(
                1 for item in interfaces if item["connection_type"] == "wired"
            ),
            "wireless_interface_count": sum(
                1 for item in interfaces if item["connection_type"] == "wireless"
            ),
            "virtual_interface_count": sum(
                1 for item in interfaces if item["connection_type"] == "virtual"
            ),
            "default_gateway_configured": any(item["gateways"] for item in interfaces),
            "dns_configured": any(item["dns_servers"] for item in interfaces),
        }
        result["traffic_counters"] = {
            key: sum(item["traffic_counters"][key] for item in interfaces)
            for key in (
                "received_bytes",
                "sent_bytes",
                "received_packets",
                "sent_packets",
                "received_errors",
                "sent_errors",
                "received_discarded",
                "sent_discarded",
            )
        }
        if wrapped_payload:
            raw_gateway = raw_payload.get("gateway_probe")
            if not isinstance(raw_gateway, dict):
                raw_gateway = {}
            gateway_state = str(raw_gateway.get("state") or "unavailable").lower()
            if gateway_state not in {
                "reachable",
                "inconclusive",
                "not_configured",
                "unavailable",
            }:
                gateway_state = "unavailable"
            try:
                latency_ms = float(raw_gateway.get("latency_ms"))
            except (TypeError, ValueError, OverflowError):
                latency_ms = None
            if latency_ms is not None and not 0 <= latency_ms <= 60000:
                latency_ms = None
            result["gateway_probe"] = {
                "state": gateway_state,
                "latency_ms": round(latency_ms, 2) if latency_ms is not None else None,
            }

            own_addresses = {
                address for interface in interfaces for address in interface.get("ipv4", [])
            }
            aliases = {interface["alias"] for interface in interfaces}
            raw_devices = raw_payload.get("lan_devices")
            if not isinstance(raw_devices, list):
                raw_devices = []
            devices = []
            seen_device_ids = set()
            for raw in raw_devices[:MAX_LAN_DEVICES]:
                if not isinstance(raw, dict):
                    continue
                ipv4 = _safe_private_ipv4(raw.get("ip_address"))
                alias = _safe_text(raw.get("interface_alias"), 80)
                state = str(raw.get("state") or "").lower()
                if (
                    not ipv4
                    or ipv4 in own_addresses
                    or not alias
                    or alias not in aliases
                    or state not in {"reachable", "delay", "probe"}
                ):
                    continue
                device_id = _device_id(
                    self.identifier_key,
                    raw.get("link_layer_address"),
                    ipv4,
                    alias,
                )
                if device_id in seen_device_ids:
                    continue
                seen_device_ids.add(device_id)
                devices.append(
                    {
                        "device_id": device_id,
                        "ipv4": ipv4,
                        "interface_alias": alias,
                        "state": state,
                    }
                )
            lan_state = str(raw_payload.get("lan_visibility_state") or "unavailable").lower()
            if lan_state not in {"partial", "unavailable"}:
                lan_state = "unavailable"
            result["lan_devices"] = devices
            result["lan_visibility"] = {
                "state": lan_state,
                "method": "windows_neighbor_cache",
                "device_count": len(devices),
            }

            raw_applications = raw_payload.get("local_applications")
            if not isinstance(raw_applications, list):
                raw_applications = []
            application_counts = {}
            for raw in raw_applications[:MAX_LOCAL_APPLICATIONS]:
                if not isinstance(raw, dict):
                    continue
                name = _safe_text(raw.get("name"), 80)
                count = _safe_counter(raw.get("connection_count"))
                if not name or count <= 0:
                    continue
                application_counts[name] = min(
                    10000,
                    application_counts.get(name, 0) + count,
                )
            applications = [
                {"name": name, "connection_count": application_counts[name]}
                for name in sorted(application_counts)[:MAX_LOCAL_APPLICATIONS]
            ]
            application_state = str(
                raw_payload.get("application_visibility_state") or "unavailable"
            ).lower()
            if application_state not in {"available", "unavailable"}:
                application_state = "unavailable"
            result["local_applications"] = applications
            result["application_visibility"] = {
                "state": application_state,
                "scope": "this_host_established_tcp_only",
                "application_count": len(applications),
            }
        return result
