# Copyright (C) 2020 iDigitalFlame
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

import math
import datetime
import logging
from scorebot_core_lite.models import Game, GameTeam, Host, Service, Content, GameCompromise, GameCompromiseHost, GameTicket, ScoreAudit, ScoreHistory

logger = logging.getLogger("scorebot_core_lite.scoring.engine")

def score_round(session, game_id: int):
    """Executes a single scoring round for the given game."""
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    game = session.query(Game).filter(Game.id == game_id).first()
    if not game or game.status != 1:  # Only running games
        return

    logger.info(f"Starting scoring round for game {game.name} (ID: {game.id})")

    # 1. Calculate host/service uptimes (read phase)
    from scorebot_core_lite.models import Purchase
    uptime_scores = {}  # team_id -> list of (host_fqdn, host_score)
    hosts_to_touch = []

    for team in game.teams:
        purchases = session.query(Purchase.item).filter(Purchase.team_id == team.id).all()
        purchased_items = {p.item.lower() for p in purchases}
        team_uptime_entries = []
        for host in team.hosts:
            if not host.check_accessible(purchased_items):
                continue
            if not host.online:
                hosts_to_touch.append(host)
                continue

            from collections import defaultdict
            app_groups = defaultdict(list)
            for service in host.services:
                if service.application.lower() == "beacon":
                    continue
                app_groups[service.application.lower()].append(service)

            host_score = 0
            for app_name, services in app_groups.items():
                group_max_score = 0
                for service in services:
                    is_active = (service.status == 0)
                    is_yellow = (service.status == 4)
                    if service.bonus and not service.bonus_started:
                        is_active = False
                        is_yellow = False

                    svc_score = 0
                    if is_active:
                        if service.content:
                            fraction = max(0, min(100, service.content.status))
                            svc_score = math.floor(service.value * (fraction / 100.0))
                        else:
                            svc_score = service.value
                    elif is_yellow:
                        svc_score = math.floor(service.value * 0.5)

                    if svc_score > group_max_score:
                        group_max_score = svc_score

                host_score += group_max_score

            if host_score > 0:
                team_uptime_entries.append((host.fqdn, host_score))
            hosts_to_touch.append(host)

        if team_uptime_entries:
            uptime_scores[team.id] = team_uptime_entries

    # 2. Gather active beacons (read phase)
    active_compromises = session.query(GameCompromise).join(GameTeam, GameCompromise.attacker_team_id == GameTeam.id).filter(
        GameTeam.game_id == game.id,
        GameCompromise.finish == None
    ).all()
    beacon_ticks = []  # list of (attacker_team_id, victim_team_id, attacker_name, victim_name, ip)
    for compromise in active_compromises:
        for ch in compromise.hosts:
            beacon_ticks.append((
                compromise.attacker_team_id,
                ch.team_id if ch.team else None,
                compromise.attacker.name if compromise.attacker else "Unknown",
                ch.team.name if ch.team else "Unknown",
                ch.ip
            ))

    # Lock GameTeam rows briefly for write application
    session.query(GameTeam).filter(
        GameTeam.game_id == game_id
    ).with_for_update().all()

    # Apply uptime scores & ScoreAudits
    for team in game.teams:
        if team.id in uptime_scores:
            for host_fqdn, host_score in uptime_scores[team.id]:
                team.score_uptime += host_score
                session.add(ScoreAudit(
                    team_id=team.id,
                    source="UPTIME",
                    amount=host_score,
                    description=f"Uptime scored for {host_fqdn}"
                ))

    # Apply per-round beacon scoring (awards for attacker, penalties for victim)
    for attacker_team_id, victim_team_id, attacker_name, victim_name, ch_ip in beacon_ticks:
        attacker_team = next((t for t in game.teams if t.id == attacker_team_id), None)
        if attacker_team:
            attacker_team.score_beacons += game.beacon_value
            session.add(ScoreAudit(
                team_id=attacker_team.id,
                source="BEACON-ATTACKER",
                amount=game.beacon_value,
                description=f"Active beacon on {ch_ip} ({victim_name})"
            ))

        if victim_team_id:
            victim_team = next((t for t in game.teams if t.id == victim_team_id), None)
            if victim_team:
                victim_team.score_beacons -= game.beacon_value
                session.add(ScoreAudit(
                    team_id=victim_team.id,
                    source="BEACON-VICTIM",
                    amount=-game.beacon_value,
                    description=f"Compromised by {attacker_name} on {ch_ip}"
                ))

    for host in hosts_to_touch:
        host.scored = now

    # 3. Score open tickets
    # In the refactored ticket scoring model, open tickets do not accumulate penalty points.
    # Teams are awarded the point value of a ticket only when the Gray team closes it.
    pass

    # 4. Take ScoreHistory Snapshots
    for team in game.teams:
        session.add(ScoreHistory(
            team_id=team.id,
            game_id=game.id,
            score_flags=team.score_flags,
            score_uptime=team.score_uptime,
            score_tickets=team.score_tickets,
            score_beacons=team.score_beacons,
            total_score=team.get_score()
        ))

    game.scored = now
    session.commit()
    logger.info(f"Finished scoring round for game {game.name}")


def zero_game_scores(session, game_id: int):
    """Zeroes out all score fields and adjustments for all teams in a game."""
    from scorebot_core_lite.models import GameTeam, ScoreAdjustment
    teams = session.query(GameTeam).filter(GameTeam.game_id == game_id).all()
    for team in teams:
        team.score_flags = 0
        team.score_uptime = 0
        team.score_tickets = 0
        team.score_beacons = 0
        # Delete all score adjustments for the team
        session.query(ScoreAdjustment).filter(ScoreAdjustment.team_id == team.id).delete()
    session.commit()
    logger.info(f"Scores zeroed out for game ID {game_id}")
