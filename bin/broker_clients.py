"""The broker client registry, bin/broker-clients.conf, as the tests read it.

bin/broker-accounts.sh reads the same file for the real outputs (password file, ACL, credentials,
rotation); the tests read it here to derive what docker-compose.yml, .env.example and the host
plugins must say instead of keeping their own lists."""

from dataclasses import dataclass, field
from pathlib import Path

REGISTRY = Path(__file__).with_name("broker-clients.conf")
BOARD = "esp32-<name>"  # the write lines every board account esp32-<name> gets


@dataclass
class Client:
    key: str
    kind: str
    target: str = ""
    unit: str = ""
    writes: list = field(default_factory=list)

    @property
    def var(self):
        return password_var(self.key)


def password_var(key):
    """MQTT_<KEY>_PASSWORD (a hyphen in a board key becomes an underscore)."""
    return f"MQTT_{key.upper().replace('-', '_')}_PASSWORD"


def load(path=REGISTRY):
    """(clients in account order, the board write lines)."""
    clients, writes = {}, {}
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        fields = line.split()
        if not fields or fields[0].startswith("#"):
            continue
        record, key, rest = fields[0], fields[1], fields[2:]
        if record == "client" and 1 <= len(rest) <= 3:
            clients[key] = Client(key, *rest)
        elif record == "write" and len(rest) == 1:
            writes.setdefault(key, []).extend(rest)
        else:
            raise ValueError(f"{path}:{number}: not a client or write line: {line}")
    board = writes.pop(BOARD, [])
    for key, topics in writes.items():
        if key not in clients:
            raise ValueError(f"{path}: write lines for unknown client {key}")
        clients[key].writes = topics
    return list(clients.values()), board


def board_writes(name, board=None):
    """The write lines of board account esp32-<name>."""
    if board is None:
        board = load()[1]
    return [topic.replace("<name>", name) for topic in board]
