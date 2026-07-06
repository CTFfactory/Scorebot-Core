# Copyright (C) 2026 luftegrof
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

"""
Game-definitions ingestion for Scorebot-Core-Lite.

Supports two source modes:

  1. Compiled DB dict  (primary, used by the REST import endpoint)
     The VAR_GAME_DEFINITIONS_DB GitHub Actions variable is a base64+gzip
     encoded JSON blob assembled by the game-definitions repo's own CI.
     Structure:
       {
         "environment": [{"environment": {...}}, ...],
         "event":       [{"event":       {...}}, ...],
         "role":        [{"role":        {...}}, ...],
         "team":        [{"team":        {...}}, ...]
       }
     The GitHub Action decodes this, extracts the relevant event/role/team
     objects via jq, and passes the subset directly in the POST body.

  2. On-disk directory (fallback / local dev)
     Reads event/*.json, role/*.json, team/*.json, environment/*.json from
     GAME_DEFINITIONS_PATH. Useful when running without CI (local testing,
     direct VM deployments where game-definitions is checked out alongside).

In both cases the output is a GameImportSpec dataclass ready for persistence.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from scorebot_core_lite import config

logger = logging.getLogger("scorebot_core_lite.ingest")

# Roles that are infrastructure-only and are never scored even if they somehow
# acquire a services list in future. Mirrors exclusion logic in generate-assets.py
# and the old Terraform locals.
INFRA_ROLES = frozenset({
    "vpc_router",
    "zoob-router",
    "scorebot",
    "gold-ns",
    "ns",
    "beacon",
    "factory",
    "health",
    "storefront",
    "cli",
    "red_kali",
    "blue_kali",
    "gold_firewall",
    "blue_firewall",
    "icinga2",
    "nagios",
})

# Only VMs with these colors have scored host stubs created for them.
BLUE_COLORS = frozenset({"blue"})


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ServiceSpec:
    """A single scored service on a host."""
    port: int
    protocol: str   # "TCP" or "UDP"
    points: int
    name: str = ""
    application: str = "ping"

    def __post_init__(self):
        if not self.name:
            self.name = f"port_{self.port}"
        self.protocol = self.protocol.upper()


@dataclass
class HostSpec:
    """A scored host stub.

    ip may be pre-populated when the pipeline knows the address at import
    time (e.g. from Proxmox guest-agent or Terraform outputs). When empty
    the host's ip column will be updated later via POST /api/hosts once the
    VM has booted and its guest-agent is reachable.
    """
    fqdn: str
    role: str
    ip: str = ""                    # optional: filled in at import or later by /api/hosts
    services: List[ServiceSpec] = field(default_factory=list)
    scheduled_start: Optional[str] = None
    scheduled_stop: Optional[str] = None
    purchasable: bool = False


@dataclass
class TeamSpec:
    """A team participating in a scored event."""
    name: str
    color: str
    subnet: str = ""
    members: List[str] = field(default_factory=list)
    hosts: List[HostSpec] = field(default_factory=list)


@dataclass
class GameImportSpec:
    """Complete specification for creating a game, its teams, hosts, and services."""
    game_name: str
    event_name: str
    environment: str
    dns_zone: str
    teams: List[TeamSpec] = field(default_factory=list)
    game_start: Optional[str] = None
    game_end: Optional[str] = None


# ---------------------------------------------------------------------------
# Source: compiled game-definitions DB dict
# ---------------------------------------------------------------------------

def _find_in_db(db: Dict[str, Any], section: str, key: str, value: str) -> Optional[dict]:
    """Look up a single record from a compiled game-definitions DB dict.

    Args:
        db:      The full compiled DB (sections: environment, event, role, team).
        section: Top-level section key, e.g. "role".
        key:     Inner field name, e.g. "name".
        value:   Value to match, e.g. "linux".

    Returns:
        The inner dict (unwrapped from the section envelope), or None.
    """
    for record in db.get(section, []):
        inner = record.get(section, {})
        if inner.get(key) == value:
            return inner
    return None


def load_from_db(
    db: Dict[str, Any],
    event_name: str,
    environment: str,
    subnets: Optional[Dict[str, str]] = None,
    host_ips: Optional[Dict[str, str]] = None,
) -> GameImportSpec:
    """Build a GameImportSpec from a compiled game-definitions DB dict.

    This is the primary ingestion path used by the REST import endpoint.
    The caller (GitHub Action) decodes VAR_GAME_DEFINITIONS_DB and passes
    the full parsed JSON here.

    Args:
        db:          Compiled game-definitions JSON parsed as a dict.
        event_name:  Event to import (e.g. "test", "bsde").
        environment: Environment name (e.g. "prod", "dev").
        subnets:     Optional per-team subnet CIDRs {"notsure": "10.x.x.0/24"}.
        host_ips:    Optional per-host IP addresses {"fqdn": "10.x.x.y"} populated
                     by the pipeline from Proxmox guest-agent or Terraform outputs.

    Raises:
        KeyError:   If the event is not found in the DB.
        ValueError: If required fields are missing.
    """
    subnets = subnets or {}
    host_ips = host_ips or {}

    event = _find_in_db(db, "event", "name", event_name)
    if event is None:
        raise KeyError(f"Event '{event_name}' not found in game-definitions DB")

    env_def = _find_in_db(db, "environment", "name", environment)
    dns_zone = (env_def or {}).get("dns", {}).get("zone", f"{environment}.ctf")

    import datetime
    game_name = f"{event_name}_{datetime.date.today().year}"

    spec = GameImportSpec(
        game_name=game_name,
        event_name=event_name,
        environment=environment,
        dns_zone=dns_zone,
        game_start=event.get("game_start"),
        game_end=event.get("game_end"),
    )

    team_names: List[str] = event.get("teams", [])
    team_map: Dict[str, TeamSpec] = {}
    for tname in team_names:
        tdef = _find_in_db(db, "team", "name", tname) or {"name": tname, "color": "blue", "members": []}
        team_map[tname] = TeamSpec(
            name=tname,
            color=tdef.get("color", "blue"),
            subnet=subnets.get(tname, ""),
            members=tdef.get("members", []),
        )

    _populate_hosts(spec, event, team_map, dns_zone, lambda role_name: _find_in_db(db, "role", "name", role_name), host_ips=host_ips)

    spec.teams = list(team_map.values())
    return spec


# ---------------------------------------------------------------------------
# Source: on-disk game-definitions directory (local dev / fallback)
# ---------------------------------------------------------------------------

def _load_json_file(path: str) -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Game-definitions file not found: {path}")
    with open(path, "r") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Failed to parse JSON at {path}: {exc}") from exc


def load_from_disk(
    event_name: str,
    environment: str,
    subnets: Optional[Dict[str, str]] = None,
    host_ips: Optional[Dict[str, str]] = None,
    base_path: Optional[str] = None,
) -> GameImportSpec:
    """Build a GameImportSpec from game-definitions files on disk.

    Reads event/{event_name}.json, role/*.json, team/*.json, and
    environment/{environment}.json from base_path (defaults to
    config.GAME_DEFINITIONS_PATH).

    Args:
        event_name:  Name of the event (e.g. "test").
        environment: Environment name (e.g. "prod").
        subnets:     Optional per-team subnet CIDRs.
        host_ips:    Optional per-host IP addresses {"fqdn": "10.x.x.y"}.
        base_path:   Override GAME_DEFINITIONS_PATH (useful for unit tests).
    """
    base = base_path or config.GAME_DEFINITIONS_PATH
    subnets = subnets or {}
    host_ips = host_ips or {}

    event_path = os.path.join(base, "event", f"{event_name}.json")
    event = _load_json_file(event_path)["event"]

    env_path = os.path.join(base, "environment", f"{environment}.json")
    env_def = {}
    if os.path.exists(env_path):
        env_def = _load_json_file(env_path).get("environment", {})
    dns_zone = env_def.get("dns", {}).get("zone", f"{environment}.ctf")

    import datetime
    game_name = f"{event_name}_{datetime.date.today().year}"

    spec = GameImportSpec(
        game_name=game_name,
        event_name=event_name,
        environment=environment,
        dns_zone=dns_zone,
        game_start=event.get("game_start"),
        game_end=event.get("game_end"),
    )

    team_names: List[str] = event.get("teams", [])
    team_map: Dict[str, TeamSpec] = {}
    for tname in team_names:
        team_path = os.path.join(base, "team", f"{tname}.json")
        tdef: dict = {}
        if os.path.exists(team_path):
            tdef = _load_json_file(team_path).get("team", {})
        team_map[tname] = TeamSpec(
            name=tname,
            color=tdef.get("color", "blue"),
            subnet=subnets.get(tname, ""),
            members=tdef.get("members", []),
        )

    def _role_loader(role_name: str) -> Optional[dict]:
        role_path = os.path.join(base, "role", f"{role_name}.json")
        if not os.path.exists(role_path):
            logger.warning("Role file not found: %s", role_path)
            return None
        return _load_json_file(role_path).get("role")

    _populate_hosts(spec, event, team_map, dns_zone, _role_loader, host_ips=host_ips)

    spec.teams = list(team_map.values())
    return spec


# ---------------------------------------------------------------------------
# Shared host-building logic
# ---------------------------------------------------------------------------

def _populate_hosts(
    spec: GameImportSpec,
    event: dict,
    team_map: Dict[str, TeamSpec],
    dns_zone: str,
    role_loader,
    host_ips: Optional[Dict[str, str]] = None,
) -> None:
    """Walk virtual_machines in an event and create scored HostSpec entries.

    This is the core cross-join that was previously expressed in scorebot.tf:
    for each blue VM × each blue team → host fqdn + services from role def.

    Args:
        spec:        GameImportSpec to mutate (teams already populated).
        event:       Parsed event dict.
        team_map:    Dict of team_name -> TeamSpec (already created).
        dns_zone:    DNS zone string used to build FQDNs.
        role_loader: Callable(role_name) -> role dict or None.
        host_ips:    Optional {fqdn: ip} map supplied by the pipeline at import
                     time. When a host's FQDN is in this map its ip field will
                     be pre-populated rather than left blank.
    """
    host_ips = host_ips or {}
    scored_count = 0
    for vm in event.get("virtual_machines", []):
        vm_instance = vm.get("vm_instance", "")
        role_name   = vm.get("role", "")
        vm_color    = vm.get("color", "")

        if not vm_instance or not role_name:
            continue

        if role_name in INFRA_ROLES:
            logger.debug("Skipping infra role VM: %s (%s)", vm_instance, role_name)
            continue

        role_def = role_loader(role_name)
        if role_def is None:
            continue

        raw_services = role_def.get("services", [])
        if not raw_services:
            logger.debug("VM %s (role: %s) has no services, skipping", vm_instance, role_name)
            continue

        services = [
            ServiceSpec(
                port=int(svc.get("port", 0)),
                protocol=svc.get("protocol", "TCP"),
                points=int(svc.get("points", 0)),
                application=svc.get("application", "ping"),
            )
            for svc in raw_services
            if svc.get("port") is not None
        ]
        if not services:
            continue

        scheduled_start = vm.get("scheduled_start")
        scheduled_stop  = vm.get("scheduled_stop")

        if vm_color in BLUE_COLORS:
            target_teams = [t for t in team_map.values() if t.color in BLUE_COLORS]
        else:
            logger.debug("VM %s color '%s' not in BLUE_COLORS, skipping", vm_instance, vm_color)
            continue

        purchasable = bool(vm.get("purchasable", False))

        for team in target_teams:
            fqdn = f"{vm_instance}.{team.name}.{spec.event_name}.{dns_zone}"
            ip = host_ips.get(fqdn, "")   # pre-populate if pipeline provided it
            host = HostSpec(
                fqdn=fqdn,
                role=role_name,
                ip=ip,
                services=services,
                scheduled_start=scheduled_start,
                scheduled_stop=scheduled_stop,
                purchasable=purchasable,
            )
            team.hosts.append(host)
            scored_count += 1
            logger.debug("  Scored host: %s ip=%s [%d services]", fqdn, ip or "(TBD)", len(services))

    logger.info(
        "Event '%s': %d teams, %d scored host stubs created",
        spec.event_name, len(team_map), scored_count,
    )
