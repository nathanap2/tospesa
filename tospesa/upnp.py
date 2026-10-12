"""Ouverture automatique d'un port sur la box (UPnP IGD), comme le font les
clients BitTorrent.

On cherche la passerelle du réseau local par SSDP, on lit sa description pour
trouver son service de connexion, puis on lui demande par SOAP d'ouvrir un
port vers cette machine et de donner l'adresse publique. Beaucoup de box le
permettent, d'autres l'ont désactivé : dans ce cas `discover` lève UPnPError
et le nœud fait sans.
"""

import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass

SSDP_ADDRESS = ("239.255.255.250", 1900)
_SEARCH_TARGETS = (
    "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
    "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
)
_SERVICES = (
    "urn:schemas-upnp-org:service:WANIPConnection:2",
    "urn:schemas-upnp-org:service:WANIPConnection:1",
    "urn:schemas-upnp-org:service:WANPPPConnection:1",
)
_MAX_RESPONSE = 256 * 1024
_ONLY_PERMANENT_LEASES = "725"


class UPnPError(Exception):
    pass


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _read(response) -> bytes:
    data = response.read(_MAX_RESPONSE + 1)
    if len(data) > _MAX_RESPONSE:
        raise UPnPError("réponse de la passerelle trop volumineuse")
    return data


@dataclass
class Gateway:
    control_url: str
    service_type: str
    local_ip: str  # adresse de cette machine vue depuis la passerelle

    def _call(self, action: str, **arguments) -> dict[str, str]:
        body = "".join(f"<{k}>{v}</{k}>" for k, v in arguments.items())
        envelope = (
            '<?xml version="1.0"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            f'<s:Body><u:{action} xmlns:u="{self.service_type}">{body}</u:{action}>'
            "</s:Body></s:Envelope>"
        )
        request = urllib.request.Request(
            self.control_url,
            data=envelope.encode(),
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPAction": f'"{self.service_type}#{action}"',
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                data = _read(response)
        except urllib.error.HTTPError as error:
            fields = _fields(_read(error))
            raise UPnPError(
                f"{action} refusé par la passerelle : {fields.get('errorCode')} "
                f"{fields.get('errorDescription', '')}".strip()
            ) from error
        except (OSError, ValueError) as error:
            raise UPnPError(f"{action} : {error}") from error
        return _fields(data)

    def external_ip(self) -> str:
        address = self._call("GetExternalIPAddress").get("NewExternalIPAddress")
        if not address:
            raise UPnPError("la passerelle ne donne pas son adresse publique")
        return address

    def add_mapping(self, port: int, lease: int = 3600) -> None:
        """Redirige le port TCP public `port` vers le même port de cette
        machine. Si la passerelle n'accepte que les redirections permanentes,
        on en demande une."""
        arguments = dict(
            NewRemoteHost="",
            NewExternalPort=port,
            NewProtocol="TCP",
            NewInternalPort=port,
            NewInternalClient=self.local_ip,
            NewEnabled=1,
            NewPortMappingDescription="tospesa",
            NewLeaseDuration=lease,
        )
        try:
            self._call("AddPortMapping", **arguments)
        except UPnPError as error:
            if _ONLY_PERMANENT_LEASES not in str(error) or lease == 0:
                raise
            self._call("AddPortMapping", **{**arguments, "NewLeaseDuration": 0})

    def delete_mapping(self, port: int) -> None:
        self._call("DeletePortMapping", NewRemoteHost="", NewExternalPort=port,
                   NewProtocol="TCP")


def _fields(data: bytes) -> dict[str, str]:
    """Éléments feuilles d'une réponse SOAP, par nom local."""
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise UPnPError(f"réponse illisible de la passerelle : {error}") from error
    return {_local_name(e.tag): (e.text or "") for e in root.iter() if len(e) == 0}


def _gateway_from(location: str) -> Gateway | None:
    with urllib.request.urlopen(location, timeout=5) as response:
        root = ElementTree.fromstring(_read(response))
    base = next(
        (e.text for e in root.iter() if _local_name(e.tag) == "URLBase" and e.text),
        location,
    )
    for service in root.iter():
        if _local_name(service.tag) != "service":
            continue
        fields = {_local_name(e.tag): (e.text or "") for e in service}
        if fields.get("serviceType") in _SERVICES and fields.get("controlURL"):
            parsed = urllib.parse.urlparse(location)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect((parsed.hostname, parsed.port or 80))
                local_ip = probe.getsockname()[0]
            return Gateway(
                urllib.parse.urljoin(base, fields["controlURL"]),
                fields["serviceType"],
                local_ip,
            )
    return None


def discover(timeout: float = 2.0, target: tuple[str, int] = SSDP_ADDRESS) -> Gateway:
    """Trouve la passerelle du réseau local. `target` ne change que pour les
    tests (une fausse passerelle en unicast)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(timeout)
        for search_target in _SEARCH_TARGETS:
            message = (
                "M-SEARCH * HTTP/1.1\r\n"
                f"HOST: {SSDP_ADDRESS[0]}:{SSDP_ADDRESS[1]}\r\n"
                'MAN: "ssdp:discover"\r\n'
                "MX: 2\r\n"
                f"ST: {search_target}\r\n\r\n"
            )
            sock.sendto(message.encode(), target)
        locations = []
        try:
            while True:
                data, _ = sock.recvfrom(65536)
                for line in data.decode(errors="replace").split("\r\n"):
                    name, _, value = line.partition(":")
                    if name.strip().lower() == "location" and value.strip() not in locations:
                        locations.append(value.strip())
        except TimeoutError:
            pass
    for location in locations:
        try:
            gateway = _gateway_from(location)
        except (OSError, ValueError, ElementTree.ParseError):
            continue
        if gateway is not None:
            return gateway
    raise UPnPError("aucune passerelle UPnP sur le réseau local")
