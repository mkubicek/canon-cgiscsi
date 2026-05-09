from __future__ import annotations

import argparse
import dataclasses
import ipaddress
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

from cgiscsi import CgiscsiClient
from commands import inquiry_cdb


@dataclasses.dataclass(frozen=True)
class DiscoveryResult:
    host: str
    peripheral_type: int
    vendor: str
    product: str
    revision: str
    raw_inquiry: bytes


def parse_inquiry_identity(data: bytes) -> tuple[int, str, str, str]:
    if len(data) < 36:
        raise ValueError(f"INQUIRY response too short: {len(data)} bytes")
    peripheral_type = data[0] & 0x1F
    vendor = data[8:16].decode("ascii", "replace").strip()
    product = data[16:32].decode("ascii", "replace").strip()
    revision = data[32:36].decode("ascii", "replace").strip()
    return peripheral_type, vendor, product, revision


def candidate_hosts_from_cidr(cidr: str, *, port: int | None = None) -> list[str]:
    network = ipaddress.ip_network(cidr, strict=False)
    hosts = network.hosts()
    if network.num_addresses == 1:
        hosts = iter([network.network_address])
    suffix = "" if port in (None, 80) else f":{port}"
    return [f"{address}{suffix}" for address in hosts]


def probe_candidate(
    host: str,
    *,
    scheme: str = "http",
    timeout: float = 1.0,
    allocation: int = 0x60,
) -> DiscoveryResult | None:
    client = CgiscsiClient(host, scheme=scheme, timeout=timeout)
    try:
        response = client.execute(
            inquiry_cdb(allocation=allocation),
            data_in_len=allocation,
            pad_cdb_to_12=True,
        )
        peripheral_type, vendor, product, revision = parse_inquiry_identity(response.data)
    except Exception:
        return None
    if not vendor and not product:
        return None
    return DiscoveryResult(
        host=host,
        peripheral_type=peripheral_type,
        vendor=vendor,
        product=product,
        revision=revision,
        raw_inquiry=response.data,
    )


def discover_candidates(
    candidates: Iterable[str],
    *,
    scheme: str = "http",
    timeout: float = 1.0,
    workers: int = 32,
    allocation: int = 0x60,
) -> list[DiscoveryResult]:
    candidate_list = list(dict.fromkeys(candidates))
    if not candidate_list:
        return []

    results: list[DiscoveryResult] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(
                probe_candidate,
                host,
                scheme=scheme,
                timeout=timeout,
                allocation=allocation,
            ): host
            for host in candidate_list
        }
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                results.append(result)
    return sorted(results, key=lambda item: item.host)


def build_candidates(args: argparse.Namespace) -> list[str]:
    candidates: list[str] = []
    for cidr in args.cidr:
        candidates.extend(candidate_hosts_from_cidr(cidr, port=args.port))
    candidates.extend(args.candidate)
    return candidates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Discover Canon cgiscsi scanners by probing INQUIRY on candidate hosts"
    )
    parser.add_argument("--cidr", action="append", default=[], help="CIDR subnet to scan, for example 192.168.1.0/24")
    parser.add_argument("--candidate", action="append", default=[], help="single host or host:port to probe")
    parser.add_argument("--port", type=int, default=80, help="port to append to CIDR-generated hosts")
    parser.add_argument("--scheme", choices=["http", "https"], default="http")
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--allocation", type=lambda s: int(s, 0), default=0x60)
    args = parser.parse_args(argv)

    candidates = build_candidates(args)
    if not candidates:
        parser.error("provide at least one --cidr or --candidate")

    for result in discover_candidates(
        candidates,
        scheme=args.scheme,
        timeout=args.timeout,
        workers=args.workers,
        allocation=args.allocation,
    ):
        print(
            f"{result.host}\tperipheral=0x{result.peripheral_type:02x}\t"
            f"vendor={result.vendor}\tproduct={result.product}\trevision={result.revision}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
