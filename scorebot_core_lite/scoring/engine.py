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

    # Lock all GameTeam rows for this game before any score modification.
    # SQLAlchemy's identity map ensures game.teams will return these same
    # already-locked objects, so no further locking is needed below.
    # This serialises concurrent API score writes (beacon check-ins, flag
    # captures, store purchases) against this scoring tick.
    session.query(GameTeam).filter(
        GameTeam.game_id == game_id
    ).with_for_update().all()

    # 1. Score host/service uptimes
    from scorebot_core_lite.models import Purchase
    for team in game.teams:
        purchases = session.query(Purchase.item).filter(Purchase.team_id == team.id).all()
        purchased_items = {p.item.lower() for p in purchases}
        for host in team.hosts:
            if not host.check_accessible(purchased_items):
                continue
            # Original scorebot: Host.get_score() returns 0 if not self.online.
            # Only score services if the host is marked online by the monitor.
            if not host.online:
                host.scored = now
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
                team.score_uptime += host_score
                session.add(ScoreAudit(
                    team_id=team.id,
                    source="UPTIME",
                    amount=host_score,
                    description=f"Uptime scored for {host.fqdn}"
                ))
                logger.debug(f"Team {team.name} Host {host.fqdn} scored +{host_score} uptime points")
            host.scored = now

    # 2. Score active beacons
    # Per-round beacon scoring matches the original scorebot (GameCompromise.round_score):
    #   - Victim team loses beacon_value points every round while the beacon is active.
    #   - Attacker team does NOT gain per-round points; the attacker already received a
    #     one-time bonus (beacon_value) when the beacon was first registered in the API.
    active_compromises = session.query(GameCompromise).join(GameTeam, GameCompromise.attacker_team_id == GameTeam.id).filter(
        GameTeam.game_id == game.id,
        GameCompromise.finish == None
    ).all()

    for compromise in active_compromises:
        # Find the compromise host information
        for ch in compromise.hosts:
            # Deduct points from compromised host's team only
            victim_team = ch.team
            if victim_team:
                victim_team.score_beacons -= game.beacon_value
                session.add(ScoreAudit(
                    team_id=victim_team.id,
                    source="BEACON-VICTIM",
                    amount=-game.beacon_value,
                    description=f"Compromised by {compromise.attacker.name} on {ch.ip}"
                ))
                logger.debug(f"Victim Team {victim_team.name} deducted {game.beacon_value} points due to active beacon on host {ch.ip} (attacker: {compromise.attacker.name})")

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
