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
from scorebot_core_lite.models import Game, GameTeam, Host, Service, Content, GameCompromise, GameCompromiseHost, GameTicket

logger = logging.getLogger("scorebot_core_lite.scoring.engine")

def score_round(session, game_id: int):
    """Executes a single scoring round for the given game."""
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    game = session.query(Game).filter(Game.id == game_id).first()
    if not game or game.status != 1:  # Only running games
        return

    logger.info(f"Starting scoring round for game {game.name} (ID: {game.id})")

    # 1. Score host/service uptimes
    for team in game.teams:
        for host in team.hosts:
            host_score = 0
            for service in host.services:
                # Service status 0 is UP/Green
                is_active = (service.status == 0)
                if service.bonus and not service.bonus_started:
                    is_active = False

                if is_active:
                    if service.content:
                        # Content status is fraction of 100
                        fraction = max(0, min(100, service.content.status))
                        host_score += math.floor(service.value * (fraction / 100.0))
                    else:
                        host_score += service.value

            if host_score > 0:
                team.score_uptime += host_score
                logger.debug(f"Team {team.name} Host {host.fqdn} scored +{host_score} uptime points")
            host.scored = now

    # 2. Score active beacons
    # Beacons deduct score_beacons from victim (compromised host's team)
    active_compromises = session.query(GameCompromise).join(GameTeam, GameCompromise.attacker_team_id == GameTeam.id).filter(
        GameTeam.game_id == game.id,
        GameCompromise.finish == None
    ).all()

    for compromise in active_compromises:
        # Find the compromise host information
        for ch in compromise.hosts:
            # Deduct points from compromised host's team
            victim_team = ch.team
            if victim_team:
                victim_team.score_beacons -= game.beacon_value
                # Award points to the attacker team
                compromise.attacker.score_beacons += game.beacon_value
                logger.debug(f"Attacker Team {compromise.attacker.name} scored +{game.beacon_value} and Victim Team {victim_team.name} deducted {game.beacon_value} points due to active beacon on host {ch.ip}")

    # 3. Score open tickets
    for team in game.teams:
        for ticket in team.tickets:
            if not ticket.closed:
                open_time = (now - ticket.started).total_seconds()
                if open_time >= game.ticket_max_scoring:
                    continue
                if open_time > game.ticket_grace_period:
                    if ticket.total < game.ticket_max_score:
                        ticket.total += game.ticket_cost
                        team.score_tickets -= game.ticket_cost
                        logger.debug(f"Team {team.name} Ticket {ticket.name} scored: ticket total cost={ticket.total}, team score ticket deduction={game.ticket_cost}")

    game.scored = now
    session.commit()
    logger.info(f"Finished scoring round for game {game.name}")
