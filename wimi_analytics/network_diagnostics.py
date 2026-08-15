import copy
import ipaddress
import json
import subprocess
import sys
import threading
import time
from datetime import datetime


NETWORK_SCHEMA_VERSION = 1
DEFAULT_CACHE_TTL_SECONDS = 5 * 60
FAILURE_CACHE_TTL_SECONDS = 30
COLLECTOR_TIMEOUT_SECONDS = 4
MAX_COLLECTOR_OUTPUT_BYTES = 64 * 1024
MAX_INTERFACES = 16
MAX_ADDRESSES_PER_FIELD = 8

POWERSHELL_NETWORK_COMMAND = r"""
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$adapters = @{}
Get-NetAdapter -ErrorAction Stop |
    Where-Object { $_.Status -eq 'Up' } |
    ForEach-Object { $adapters[[int]$_.ifIndex] = $_ }
$items = @(
    Get-CimInstance Win32_NetworkAdapterConfiguration -Filter 'IPEnabled=True' -ErrorAction Stop |
        ForEach-Object {
            $config = $_
            $adapter = $adapters[[int]$config.InterfaceIndex]
            if ($null -ne $adapter) {
                [pscustomobject]@{
                    alias = $adapter.Name
                    profile = $null
                    status = $adapter.Status
                    link_speed = $adapter.LinkSpeed
                    ipv4 = @($config.IPAddress)
                    gateway = @($config.DefaultIPGateway)
                    dns = @($config.DNSServerSearchOrder)
                }
            }
        }
)
ConvertTo-Json -Compress -Depth 4 -InputObject $items
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


def _base_result(state, reason):
    return {
        "schema_version": NETWORK_SCHEMA_VERSION,
        "state": state,
        "reason": reason,
        "coverage": "host_configuration_only",
        "can_observe_store_traffic": False,
        "source": "windows_cim_network_configuration",
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "interfaces": [],
        "connectivity": {
            "active_interface_count": 0,
            "default_gateway_configured": False,
            "dns_configured": False,
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
    ):
        self.ttl_seconds = max(5, int(ttl_seconds))
        self.runner = runner
        self.platform_name = platform_name or sys.platform
        self.clock = clock
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
            raw_interfaces = json.loads(completed.stdout.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError):
            return _base_result("unavailable", "collector_invalid_output")

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
                    "ipv4": _safe_ip_list(raw.get("ipv4"), ipv4_only=True),
                    "gateways": _safe_ip_list(raw.get("gateway")),
                    "dns_servers": _safe_ip_list(raw.get("dns")),
                }
            )

        if not interfaces:
            return _base_result("unavailable", "no_active_interface")

        result = _base_result("active", "host_network_detected")
        result["interfaces"] = interfaces
        result["connectivity"] = {
            "active_interface_count": len(interfaces),
            "default_gateway_configured": any(item["gateways"] for item in interfaces),
            "dns_configured": any(item["dns_servers"] for item in interfaces),
        }
        return result
