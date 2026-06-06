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

import json
import uuid
import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Float
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from scorebot_core_lite import config

Base = declarative_base()

class Game(Base):
    __tablename__ = "games"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    start = Column(DateTime, nullable=True)
    finish = Column(DateTime, nullable=True)
    scored = Column(DateTime, nullable=True)
    mode = Column(Integer, default=0)
    status = Column(Integer, default=0)  # 0=created, 1=running, 2=paused, 3=finished

    # Default options directly on game
    round_time = Column(Integer, default=15)
    job_timeout = Column(Integer, default=30)
    job_cleanup_time = Column(Integer, default=300)
    flag_stolen_rate = Column(Integer, default=0)
    flag_captured_multiplier = Column(Integer, default=1)
    beacon_value = Column(Integer, default=10)
    ticket_cost = Column(Integer, default=5)
    ticket_max_score = Column(Integer, default=100)
    ticket_grace_period = Column(Integer, default=60)
    ticket_max_scoring = Column(Integer, default=3600)
    ticket_reopen_multiplier = Column(Integer, default=200)
    score_exchange_rate = Column(Integer, default=100)  # exchange rate * 100
    host_ping_ratio = Column(Integer, default=50)

    teams = relationship("GameTeam", back_populates="game", cascade="all, delete-orphan")
    events = relationship("GameEvent", back_populates="game", cascade="all, delete-orphan")

    def get_list_json(self):
        d = {"id": self.id, "mode": self.mode, "name": self.name, "status": self.status}
        if self.start:
            d["start"] = self.start.isoformat() + "Z"
        if self.finish:
            d["end"] = self.finish.isoformat() + "Z"
        return d

    def get_json_scoreboard(self):
        return {
            "name": self.name,
            "message": "",  # Deprecated notification message
            "mode": self.mode,
            "teams": [t.get_json_scoreboard() for t in self.teams if t.visible is not False],
            "events": [e.get_json_scoreboard() for e in self.events if e.timeout > datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)],
            "credit": "",
        }


class GameTeam(Base):
    __tablename__ = "game_teams"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    subnet = Column(String(90), nullable=False)
    logo = Column(String(200), default="default.png")
    offensive = Column(Boolean, default=False)
    minimal = Column(Boolean, default=False)
    color = Column(Integer, default=0)
    store = Column(Integer, unique=True, nullable=True)
    game_id = Column(Integer, ForeignKey("games.id", ondelete="CASCADE"), nullable=False)
    token = Column(String(36), unique=True, default=lambda: str(uuid.uuid4()))
    visible = Column(Boolean, default=True)

    score_flags = Column(Integer, default=0)
    score_uptime = Column(Integer, default=0)
    score_tickets = Column(Integer, default=0)
    score_beacons = Column(Integer, default=0)

    game = relationship("Game", back_populates="teams")
    hosts = relationship("Host", back_populates="team", cascade="all, delete-orphan")
    flags = relationship("Flag", foreign_keys="[Flag.team_id]", back_populates="team", cascade="all, delete-orphan")
    compromises = relationship("GameCompromise", back_populates="attacker")
    tickets = relationship("GameTicket", back_populates="team", cascade="all, delete-orphan")
    purchases = relationship("Purchase", back_populates="team", cascade="all, delete-orphan")
    beacon_tokens = relationship("GameTeamBeaconToken", back_populates="team", cascade="all, delete-orphan")
    score_adjustments = relationship("ScoreAdjustment", back_populates="team", cascade="all, delete-orphan")

    def get_score(self):
        adjustment_sum = sum(adj.amount for adj in self.score_adjustments)
        return self.score_flags + self.score_uptime + self.score_tickets + self.score_beacons + adjustment_sum

    def get_beacons(self):
        beacons = []
        for compromise in self.compromises:
            if compromise.finish is None:
                # Get the associated compromise hosts
                for ch in compromise.hosts:
                    beacons.append({
                        "id": compromise.id,
                        "team": compromise.attacker_team_id,
                        "color": f"#{hex(compromise.attacker.color).replace('0x', '').zfill(6)}",
                    })
        return beacons

    def get_json_scoreboard(self):
        # Calculate captured flags by this team (captured relationship mapping)
        from sqlalchemy.orm import object_session
        session = object_session(self) or db_session
        captured_flags_count = len(session.query(Flag).filter(Flag.captured_team_id == self.id).all())
        open_flags = len([f for f in self.flags if f.enabled and f.captured_team_id is None])
        lost_flags = len([f for f in self.flags if f.enabled and f.captured_team_id is not None])
        open_tickets = len([t for t in self.tickets if not t.closed])
        closed_tickets = len([t for t in self.tickets if t.closed])

        return {
            "id": self.id,
            "name": self.name,
            "color": f"#{hex(self.color).replace('0x', '').zfill(6)}",
            "score": {"total": self.get_score(), "health": self.score_uptime},
            "offense": self.offensive,
            "flags": {
                "open": open_flags,
                "lost": lost_flags,
                "captured": captured_flags_count,
            },
            "tickets": {
                "open": open_tickets,
                "closed": closed_tickets,
            },
            "hosts": [h.get_json_scoreboard() for h in self.hosts],
            "logo": self.logo or "default.png",
            "beacons": self.get_beacons(),
            "minimal": self.minimal,
        }

    def get_json_mapper(self):
        return {"name": self.name, "token": self.token, "id": self.id}


class Host(Base):
    __tablename__ = "hosts"

    id = Column(Integer, primary_key=True, index=True)
    fqdn = Column(String(150), nullable=False)
    online = Column(Boolean, default=False)
    name = Column(String(150), nullable=True)
    ping_min = Column(Integer, default=0)
    scored = Column(DateTime, nullable=True)
    ip = Column(String(50), nullable=True)
    ping_last = Column(Integer, default=0)
    team_id = Column(Integer, ForeignKey("game_teams.id", ondelete="CASCADE"), nullable=False)

    team = relationship("GameTeam", back_populates="hosts")
    services = relationship("Service", back_populates="host", cascade="all, delete-orphan")
    flags = relationship("Flag", back_populates="host", cascade="all, delete-orphan")

    def get_json_scoreboard(self):
        return {
            "name": self.name or (self.fqdn.split(".")[0] if "." in self.fqdn else self.fqdn),
            "id": self.id,
            "online": self.online,
            "services": [s.get_json_scoreboard() for s in self.services if s.application.lower() != "beacon"],
        }

    def get_json_job(self):
        team_idx = (self.team.id - 1) if self.team else 0
        dns_ip = f"100.64.{team_idx}.68"
        return {
            "host": {
                "fqdn": self.fqdn,
                "services": [s.get_json_job() for s in self.services],
            },
            "dns": [dns_ip],
            "timeout": self.team.game.round_time if (self.team and self.team.game) else 15,
        }


class Service(Base):
    __tablename__ = "services"

    id = Column(Integer, primary_key=True, index=True)
    port = Column(Integer, nullable=False)
    name = Column(String(64), nullable=False)
    bonus = Column(Boolean, default=False)
    value = Column(Integer, default=50)
    bonus_started = Column(Boolean, default=False)
    host_id = Column(Integer, ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False)
    application = Column(String(64), default="ping")
    protocol = Column(Integer, default=1)  # 1=tcp, 2=udp
    status = Column(Integer, default=2)  # 0=up/green, 1=down/red, 2=timeout, 3=refused, 4=yellow

    host = relationship("Host", back_populates="services")
    content = relationship("Content", uselist=False, back_populates="service", cascade="all, delete-orphan")

    def get_json_scoreboard(self):
        proto_char = "t" if self.protocol == 1 else "u"
        status_color = "green" if self.status == 0 else "yellow" if self.status == 4 else "red"
        return {
            "status": status_color,
            "id": self.id,
            "protocol": proto_char,
            "port": self.port,
            "bonus": self.bonus,
        }

    def get_json_job(self):
        return {
            "port": self.port,
            "application": self.application,
            "protocol": "tcp" if self.protocol == 1 else "udp",
            "content": self.content.get_json_job() if self.content else None,
        }


class Content(Base):
    __tablename__ = "contents"

    id = Column(Integer, primary_key=True, index=True)
    service_id = Column(Integer, ForeignKey("services.id", ondelete="CASCADE"), nullable=False, unique=True)
    data = Column(Text, nullable=True)
    status = Column(Integer, default=0)
    type = Column(String(64), default="default")

    service = relationship("Service", back_populates="content")

    def get_json_job(self):
        try:
            content_parsed = json.loads(self.data)
        except (TypeError, ValueError):
            content_parsed = self.data
        return {"type": self.type, "content": content_parsed}


class Flag(Base):
    __tablename__ = "flags"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    flag = Column(String(120), nullable=False)
    enabled = Column(Boolean, default=True)
    description = Column(Text, nullable=False)
    value = Column(Integer, default=100)
    host_id = Column(Integer, ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False)
    team_id = Column(Integer, ForeignKey("game_teams.id", ondelete="CASCADE"), nullable=False)
    captured_team_id = Column(Integer, ForeignKey("game_teams.id", ondelete="SET NULL"), nullable=True)

    host = relationship("Host", back_populates="flags")
    team = relationship("GameTeam", foreign_keys=[team_id], back_populates="flags")

    @classmethod
    def query_by_captured_team(cls, session, team_id):
        # Helper to query flags captured by a specific team ID
        s = session or db_session
        return s.query(cls).filter(cls.captured_team_id == team_id).all()


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    start = Column(DateTime, default=datetime.datetime.utcnow)
    finish = Column(DateTime, nullable=True)
    monitor_name = Column(String(100), nullable=False)
    host_id = Column(Integer, ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False)

    host = relationship("Host")


class GameEvent(Base):
    __tablename__ = "game_events"

    id = Column(Integer, primary_key=True, index=True)
    timeout = Column(DateTime, nullable=False)
    data = Column(Text, nullable=True)
    game_id = Column(Integer, ForeignKey("games.id", ondelete="CASCADE"), nullable=False)
    type = Column(Integer, default=0)

    game = relationship("Game", back_populates="events")

    def get_json_scoreboard(self):
        try:
            event_data = json.loads(self.data)
        except (TypeError, ValueError):
            event_data = self.data
        return {"id": self.id, "type": self.type, "data": event_data}


class GameTicket(Base):
    __tablename__ = "game_tickets"

    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, nullable=False)
    name = Column(String(150), nullable=False)
    closed = Column(Boolean, default=False)
    started = Column(DateTime, default=datetime.datetime.utcnow)
    total = Column(Integer, default=0)
    description = Column(Text, nullable=False)
    team_id = Column(Integer, ForeignKey("game_teams.id", ondelete="CASCADE"), nullable=False)
    type = Column(Integer, default=0)

    team = relationship("GameTeam", back_populates="tickets")


class Purchase(Base):
    __tablename__ = "purchases"

    id = Column(Integer, primary_key=True, index=True)
    item = Column(String(150), nullable=False)
    amount = Column(Integer, nullable=False)
    date = Column(DateTime, default=datetime.datetime.utcnow)
    team_id = Column(Integer, ForeignKey("game_teams.id", ondelete="CASCADE"), nullable=False)

    team = relationship("GameTeam", back_populates="purchases")


class GameCompromise(Base):
    __tablename__ = "game_compromises"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(36), nullable=False)
    attacker_team_id = Column(Integer, ForeignKey("game_teams.id", ondelete="CASCADE"), nullable=False)
    start = Column(DateTime, default=datetime.datetime.utcnow)
    finish = Column(DateTime, nullable=True)

    attacker = relationship("GameTeam", back_populates="compromises")
    hosts = relationship("GameCompromiseHost", back_populates="compromise", cascade="all, delete-orphan")


class GameCompromiseHost(Base):
    __tablename__ = "game_compromise_hosts"

    id = Column(Integer, primary_key=True, index=True)
    ip = Column(String(50), nullable=False)
    team_id = Column(Integer, ForeignKey("game_teams.id", ondelete="CASCADE"), nullable=False)
    host_id = Column(Integer, ForeignKey("hosts.id", ondelete="SET NULL"), nullable=True)
    beacon_id = Column(Integer, ForeignKey("game_compromises.id", ondelete="CASCADE"), nullable=False)
    checkin = Column(DateTime, default=datetime.datetime.utcnow)

    compromise = relationship("GameCompromise", back_populates="hosts")
    host = relationship("Host")
    team = relationship("GameTeam")

class GameTeamBeaconToken(Base):
    __tablename__ = "game_team_beacon_tokens"

    id = Column(Integer, primary_key=True, index=True)
    team_id = Column(Integer, ForeignKey("game_teams.id", ondelete="CASCADE"), nullable=False)
    token = Column(String(36), unique=True, default=lambda: str(uuid.uuid4()))

    team = relationship("GameTeam", back_populates="beacon_tokens")


class ScoreAdjustment(Base):
    __tablename__ = "score_adjustments"

    id = Column(Integer, primary_key=True, index=True)
    team_id = Column(Integer, ForeignKey("game_teams.id", ondelete="CASCADE"), nullable=False)
    amount = Column(Integer, nullable=False)
    reason = Column(String(500), nullable=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

    team = relationship("GameTeam", back_populates="score_adjustments")


engine = create_engine(config.DATABASE_URL, connect_args={"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db_session = SessionLocal()

def init_db():
    Base.metadata.create_all(bind=engine)
