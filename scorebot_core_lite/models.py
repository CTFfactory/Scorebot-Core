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
    create_engine, Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Float, text, UniqueConstraint
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
    round_time = Column(Integer, default=300)
    job_timeout = Column(Integer, default=300)
    job_cleanup_time = Column(Integer, default=900)
    flag_stolen_rate = Column(Integer, default=8400)
    flag_captured_multiplier = Column(Integer, default=300)
    beacon_value = Column(Integer, default=100)
    ticket_cost = Column(Integer, default=125)
    ticket_max_score = Column(Integer, default=6000)
    ticket_grace_period = Column(Integer, default=900)
    ticket_max_scoring = Column(Integer, default=14400)
    ticket_reopen_multiplier = Column(Integer, default=10)
    score_exchange_rate = Column(Integer, default=100)  # exchange rate * 100
    host_ping_ratio = Column(Integer, default=50)
    beacon_time = Column(Integer, default=300)  # Beacon expiry timeout (seconds); original Options.beacon_time default = 300
    zero_out_time = Column(DateTime, nullable=True)
    authenticated_checks = Column(Boolean, default=False)

    teams = relationship("GameTeam", back_populates="game", cascade="all, delete-orphan")
    events = relationship("GameEvent", back_populates="game", cascade="all, delete-orphan")
    ports = relationship("GamePort", back_populates="game", cascade="all, delete-orphan")

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
    compromises = relationship("GameCompromise", back_populates="attacker", cascade="all, delete-orphan")
    tickets = relationship("GameTicket", back_populates="team", cascade="all, delete-orphan")
    purchases = relationship("Purchase", back_populates="team", cascade="all, delete-orphan")
    beacon_tokens = relationship("GameTeamBeaconToken", back_populates="team", cascade="all, delete-orphan")
    score_adjustments = relationship("ScoreAdjustment", back_populates="team", cascade="all, delete-orphan")
    compromised_hosts = relationship("GameCompromiseHost", foreign_keys="[GameCompromiseHost.team_id]", back_populates="team", cascade="all, delete-orphan")

    def get_score(self):
        adjustment_sum = sum(adj.amount for adj in self.score_adjustments)
        return self.score_flags + self.score_uptime + self.score_tickets + self.score_beacons + adjustment_sum

    def get_beacons(self):
        beacons = []
        for ch in self.compromised_hosts:
            if ch.compromise.finish is None:
                beacons.append({
                    "id": ch.compromise.id,
                    "team": ch.compromise.attacker_team_id,
                    "color": f"#{hex(ch.compromise.attacker.color).replace('0x', '').zfill(6)}",
                })
        return beacons

    def get_json_scoreboard(self):
        # Calculate captured flags by this team (captured relationship mapping)
        from sqlalchemy.orm import object_session
        session = object_session(self)
        close_session = False
        if session is None:
            session = SessionLocal()
            close_session = True
        try:
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
            "hosts": [h.get_json_scoreboard() for h in self.hosts if h.is_accessible],
            "logo": self.logo or "default.png",
            "beacons": self.get_beacons(),
            "minimal": self.minimal,
        }
        finally:
            if close_session:
                session.close()

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
    purchasable = Column(Boolean, default=False)
    team_id = Column(Integer, ForeignKey("game_teams.id", ondelete="CASCADE"), nullable=True)

    team = relationship("GameTeam", back_populates="hosts")
    services = relationship("Service", back_populates="host", cascade="all, delete-orphan")
    flags = relationship("Flag", back_populates="host", cascade="all, delete-orphan")

    @property
    def is_accessible(self):
        """Returns True if the host is not purchasable, or if it is purchasable and has been purchased."""
        if not self.purchasable:
            return True
        if not self.team:
            return False
        # Check if any purchase matches "VM Deployment: <name>"
        expected_item = f"VM Deployment: {self.name}".lower()
        for p in self.team.purchases:
            if p.item.lower() == expected_item:
                return True
        return False

    def get_json_scoreboard(self):
        return {
            "name": self.name or (self.fqdn.split(".")[0] if "." in self.fqdn else self.fqdn),
            "id": self.id,
            "online": self.online,
            "services": [s.get_json_scoreboard() for s in self.services if s.application.lower() != "beacon"],
        }

    def get_json_job(self):
        dns_ip = None
        if self.team and self.team.subnet:
            parts = self.team.subnet.split('.')
            if len(parts) >= 3:
                dns_ip = f"{parts[0]}.{parts[1]}.{parts[2]}.68"
        if not dns_ip and self.team and self.team.game:
            for t in self.team.game.teams:
                if t.subnet:
                    parts = t.subnet.split('.')
                    if len(parts) >= 3:
                        dns_ip = f"{parts[0]}.{parts[1]}.{self.team.id}.68"
                        break
        if not dns_ip:
            import os
            beacon_ip = config.BEACON_IP or os.getenv("BEACON_IP")
            if beacon_ip:
                parts = beacon_ip.split('.')
                if len(parts) >= 3:
                    team_idx = self.team.id if self.team else 1
                    dns_ip = f"{parts[0]}.{parts[1]}.{team_idx}.68"
        if not dns_ip:
            team_idx = self.team.id if self.team else 1
            dns_ip = f"100.64.{team_idx}.68"
        return {
            "host": {
                "fqdn": self.fqdn,
                "services": [s.get_json_job() for s in self.services],
            },
            "dns": [dns_ip],
            "timeout": self.team.game.round_time if (self.team and self.team.game) else 15,
            "authenticated_checks": self.team.game.authenticated_checks if (self.team and self.team.game) else False,
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
            "name": self.name,
            "application": self.application,
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
        close_session = False
        if session is None:
            session = SessionLocal()
            close_session = True
        try:
            return session.query(cls).filter(cls.captured_team_id == team_id).all()
        finally:
            if close_session:
                session.close()


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
    point_value = Column(Integer, default=0)
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
    team = relationship("GameTeam", back_populates="compromised_hosts")

class GameTeamBeaconToken(Base):
    __tablename__ = "game_team_beacon_tokens"

    id = Column(Integer, primary_key=True, index=True)
    team_id = Column(Integer, ForeignKey("game_teams.id", ondelete="CASCADE"), nullable=False)
    token = Column(String(36), unique=True, default=lambda: str(uuid.uuid4()))

    team = relationship("GameTeam", back_populates="beacon_tokens")


class GamePort(Base):
    __tablename__ = "game_ports"

    id = Column(Integer, primary_key=True, index=True)
    port = Column(Integer, nullable=False)
    game_id = Column(Integer, ForeignKey("games.id", ondelete="CASCADE"), nullable=False)

    game = relationship("Game", back_populates="ports")


class ScoreAdjustment(Base):
    __tablename__ = "score_adjustments"

    id = Column(Integer, primary_key=True, index=True)
    team_id = Column(Integer, ForeignKey("game_teams.id", ondelete="CASCADE"), nullable=False)
    amount = Column(Integer, nullable=False)
    reason = Column(String(500), nullable=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

    team = relationship("GameTeam", back_populates="score_adjustments")


class ScoreAudit(Base):
    __tablename__ = "score_audits"

    id = Column(Integer, primary_key=True, index=True)
    team_id = Column(Integer, ForeignKey("game_teams.id", ondelete="CASCADE"), nullable=False)
    source = Column(String(50), nullable=False)
    amount = Column(Integer, nullable=False)
    description = Column(String(500), nullable=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

    team = relationship("GameTeam")

class ScoreHistory(Base):
    __tablename__ = "score_histories"

    id = Column(Integer, primary_key=True, index=True)
    team_id = Column(Integer, ForeignKey("game_teams.id", ondelete="CASCADE"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id", ondelete="CASCADE"), nullable=False)
    score_flags = Column(Integer, default=0)
    score_uptime = Column(Integer, default=0)
    score_tickets = Column(Integer, default=0)
    score_beacons = Column(Integer, default=0)
    total_score = Column(Integer, default=0)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

    team = relationship("GameTeam")
    game = relationship("Game")

class StorePriceOverride(Base):
    __tablename__ = "store_price_overrides"

    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id", ondelete="CASCADE"), nullable=False)
    item_id = Column(String(150), nullable=False)
    price = Column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("game_id", "item_id", name="uq_game_item_price"),
    )


if config.DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        config.DATABASE_URL,
        connect_args={"check_same_thread": False, "timeout": 30},
    )
else:
    engine = create_engine(
        config.DATABASE_URL,
        pool_size=15,
        max_overflow=25,
        pool_pre_ping=True,
        pool_timeout=30,
    )
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

from sqlalchemy import event
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if config.DATABASE_URL.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-64000")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.close()

@event.listens_for(engine, "begin")
def do_begin(conn):
    if config.DATABASE_URL.startswith("sqlite"):
        conn.exec_driver_sql("BEGIN IMMEDIATE")

def init_db():
    Base.metadata.create_all(bind=engine)
    # Best-effort schema migrations for columns added after initial deployment
    migrations = [
        "ALTER TABLE games ADD COLUMN zero_out_time DATETIME",
        "ALTER TABLE games ADD COLUMN beacon_time INTEGER DEFAULT 300",
        "ALTER TABLE hosts ADD COLUMN purchasable BOOLEAN DEFAULT FALSE",
        "ALTER TABLE game_tickets ADD COLUMN point_value INTEGER DEFAULT 0",
        "ALTER TABLE games ADD COLUMN authenticated_checks BOOLEAN DEFAULT FALSE",
    ]
    for stmt in migrations:
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except Exception:
            pass  # Column already exists or DB doesn't support ALTER TABLE


import logging
from sqlalchemy import event

@event.listens_for(ScoreAudit, 'after_insert')
def log_score_audit(mapper, connection, target):
    logger = logging.getLogger("scorebot_core_lite")
    logger.info(
        "SCORE_AUDIT: team_id=%s source=%s amount=%d desc=\"%s\"",
        target.team_id,
        target.source,
        target.amount,
        target.description or ""
    )

