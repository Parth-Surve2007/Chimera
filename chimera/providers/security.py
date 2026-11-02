"""Validation and security policy for provider network endpoints."""
from __future__ import annotations

import ipaddress
import urllib.parse
from .errors import ProviderConnectionError

# Allowlisted local loopback hosts (strict client connection destinations only)
ALLOWLISTED_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Prohibited dangerous cloud metadata IPs & prefixes
PROHIBITED_METADATA_IPS = {
    "169.254.169.254",   # AWS / GCP / Azure IMDS
    "169.254.169.253",   # AWS DNS / DHCP
    "100.100.100.200",   # Alibaba IMDS
    "fd00:ec2::254",     # AWS IMDSv6
}


def _validate_ip_object(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, allow_private_network: bool) -> tuple[bool, str]:
    """Validate an IP address object against SSRF, metadata, and broadcast rules."""
    # Reject 0.0.0.0 and :: (unspecified server bind addresses, never valid client destinations)
    if ip.is_unspecified:
        return False, f"Unspecified address '{ip}' (0.0.0.0 / ::) is not a permitted outbound connection destination."

    # Check IPv4-mapped IPv6 (e.g. ::ffff:169.254.169.254 or ::ffff:0.0.0.0)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        mapped = ip.ipv4_mapped
        if mapped.is_unspecified:
            return False, f"IPv4-mapped unspecified address '{mapped}' is prohibited as an outbound destination."
        if str(mapped) in PROHIBITED_METADATA_IPS:
            return False, f"Access to IPv4-mapped cloud metadata endpoint {mapped} is prohibited."
        if mapped.is_link_local:
            return False, f"Link-local address {mapped} is prohibited to prevent cloud metadata SSRF."
        if mapped.is_loopback:
            return True, "IPv4-mapped loopback authorized"
        if mapped.is_private and not allow_private_network:
            return False, f"Private network address {mapped} requires explicit authorization."
        return True, "IPv4-mapped endpoint authorized"

    # Reject cloud metadata and link-local ranges (169.254.0.0/16, fe80::/10)
    if str(ip) in PROHIBITED_METADATA_IPS:
        return False, f"Access to cloud metadata endpoint {ip} is prohibited for security."
    if ip.is_link_local:
        return False, f"Link-local address {ip} is prohibited to prevent cloud metadata SSRF."

    # Reject multicast & reserved
    if ip.is_multicast:
        return False, f"Multicast address {ip} is prohibited."
    if ip.is_reserved:
        return False, f"Reserved address {ip} is prohibited."

    # Loopback is authorized for local inference (llama.cpp, Ollama, LM Studio, test servers)
    if ip.is_loopback:
        return True, "Loopback IP authorized"

    if ip.is_private and not allow_private_network:
        return False, f"Private network address {ip} requires explicit authorization."

    return True, "Valid IP endpoint"


def validate_provider_endpoint(url: str, allow_private_network: bool = True) -> tuple[bool, str]:
    """Validate a provider endpoint URL for protocol, hostname, port, and SSRF security.
    
    Returns:
        (is_valid, reason)
    """
    if not url or not isinstance(url, str):
        return False, "Endpoint URL is empty or invalid"

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, f"Unsupported URL scheme: '{parsed.scheme}'. Only HTTP and HTTPS are permitted."

    host = (parsed.hostname or "").lower()
    if not host:
        return False, "Endpoint URL missing hostname"

    # Explicitly reject 0.0.0.0 string
    if host in ("0.0.0.0", "0", "0000"):
        return False, "Unspecified address '0.0.0.0' is a server bind address and cannot be used as an outbound destination."

    # Port validation
    try:
        port = parsed.port
        if port is not None and not (1 <= port <= 65535):
            return False, f"Port {port} is out of valid range (1-65535)."
    except ValueError as exc:
        return False, f"Invalid port in URL: {exc}"

    # Cloud metadata prevention (direct hostname match)
    if host in PROHIBITED_METADATA_IPS:
        return False, f"Access to cloud metadata endpoint {host} is prohibited for security."

    # Loopback names are allowed for local inference (llama-server, Ollama, LM Studio, test servers)
    if host in ALLOWLISTED_LOCAL_HOSTS:
        return True, "Local loopback endpoint authorized"

    # IP address parsing & checking
    try:
        ip = ipaddress.ip_address(host)
        return _validate_ip_object(ip, allow_private_network)
    except ValueError:
        # Domain name
        pass

    if parsed.scheme != "https" and not host.endswith(".local") and not host.endswith(".internal"):
        return False, f"Remote endpoint '{host}' must use HTTPS for credential and data protection."

    return True, "Valid secure remote endpoint"


def resolve_and_validate_endpoint(url: str, allow_private_network: bool = True) -> tuple[bool, str, list[str]]:
    """Validate URL syntax and resolve DNS hostnames to verify resolved IPs against SSRF/metadata targets.
    
    Prevents DNS rebinding and internal IP bypass attacks.
    Returns:
        (is_valid, reason, resolved_ips)
    """
    valid, reason = validate_provider_endpoint(url, allow_private_network=allow_private_network)
    if not valid:
        return False, reason, []

    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()

    if host in ALLOWLISTED_LOCAL_HOSTS:
        return True, "Local loopback authorized without DNS lookup", ["127.0.0.1"]

    import socket
    try:
        addrinfo = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
        resolved_ips = list({info[4][0] for info in addrinfo})
    except socket.gaierror as exc:
        return False, f"DNS resolution failed for '{host}': {exc}", []

    for ip_str in resolved_ips:
        try:
            ip_obj = ipaddress.ip_address(ip_str)
            ip_valid, ip_reason = _validate_ip_object(ip_obj, allow_private_network=allow_private_network)
            if not ip_valid:
                return False, f"Host '{host}' resolved to prohibited address {ip_str}: {ip_reason}", resolved_ips
        except ValueError:
            return False, f"Resolved IP {ip_str} is invalid", resolved_ips

    return True, f"Endpoint and resolved addresses {resolved_ips} verified", resolved_ips

