"""mDNS TXT record generation and optional zeroconf publishing."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass

from .config import EsclConfig, ScannerConfig


USCAN_SERVICE_TYPE = "_uscan._tcp.local."


def uscan_txt_records(
    *,
    escl: EsclConfig | None = None,
    scanner: ScannerConfig | None = None,
) -> dict[str, str]:
    escl = escl or EsclConfig()
    scanner = scanner or ScannerConfig()
    return {
        "txtvers": "1",
        "rs": escl.root_resource.strip("/") or "eSCL",
        "ty": scanner.model_name,
        "note": "Scan inbox adapter",
        "pdl": "image/jpeg",
        "is": "adf",
        "duplex": "T",
        "cs": "grayscale",
        "adminurl": escl.admin_url,
        "UUID": escl.uuid,
        "mopria-certified-scan": "1.2",
    }


@dataclass
class MdnsPublisher:
    escl: EsclConfig
    scanner: ScannerConfig
    hostname: str | None = None

    def __post_init__(self) -> None:
        self._zeroconf = None
        self._service_info = None

    def start(self) -> None:
        try:
            from zeroconf import ServiceInfo, Zeroconf
        except ImportError as exc:
            raise RuntimeError("mDNS publishing requires the optional zeroconf package") from exc

        host = self.hostname or socket.gethostname()
        if not host.endswith(".local."):
            host = f"{host}.local."
        properties = uscan_txt_records(escl=self.escl, scanner=self.scanner)
        name = f"{self.escl.service_name}.{USCAN_SERVICE_TYPE}"
        advertise_ip = os.environ.get("AIRSCAN_ADVERTISE_IP")
        addresses = [socket.inet_aton(advertise_ip)] if advertise_ip else []
        info = ServiceInfo(
            USCAN_SERVICE_TYPE,
            name,
            addresses=addresses,
            port=self.escl.port,
            properties=properties,
            server=host,
        )
        zeroconf = Zeroconf()
        zeroconf.register_service(info)
        self._zeroconf = zeroconf
        self._service_info = info

    def stop(self) -> None:
        if self._zeroconf is None:
            return
        self._zeroconf.unregister_service(self._service_info)
        self._zeroconf.close()
        self._zeroconf = None
        self._service_info = None
