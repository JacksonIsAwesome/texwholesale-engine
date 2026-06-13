"""
main.py — TexWholesale Engine
A single-service FastAPI app for Texas real estate wholesaling.

Local dev (Mac M4):   uvicorn main:app --reload
Deploy (Railway):     uses Dockerfile + railway.toml

SQLite locally, Postgres in prod via DATABASE_URL.
All external integrations degrade gracefully when keys/sources are missing.
"""

from __future__ import annotations

import csv
import io
import json
import os
import statistics
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import Session, declarative_base, sessionmaker

import sources as source_engine

load_dotenv()

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

APP_NAME = "TexWholesale Engine"
APP_VERSION = "1.1.0"

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./texwholesale.db")
# Railway/Heroku hand out postgres:// ; SQLAlchemy 2.x wants postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
USPS_USER_ID = os.getenv("USPS_USER_ID", "").strip()
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
BATCHDATA_API_KEY = os.getenv("BATCHDATA_API_KEY", "").strip()
TRACERFY_API_KEY = os.getenv("TRACERFY_API_KEY", "").strip()
DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"

HTTP_TIMEOUT = 5.0

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

DASHBOARD_DIR = os.path.join(os.path.dirname(__file__), "dashboard")


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uid() -> str:
    return uuid.uuid4().hex[:12]


PIPELINE_STAGES = [
    "New",
    "Contacted",
    "Offer Sent",
    "Under Contract",
    "Assigned",
    "Closed",
]


class Lead(Base):
    __tablename__ = "leads"

    id = Column(String, primary_key=True, default=_uid)
    address = Column(String, nullable=False, default="")
    city = Column(String, default="")
    state = Column(String, default="TX")
    zip_code = Column(String, default="")
    county = Column(String, default="")

    owner_name = Column(String, default="")
    owner_address = Column(String, default="")
    owner_phone = Column(String, default="")
    owner_email = Column(String, default="")

    est_value = Column(Float, default=0.0)
    est_equity_pct = Column(Float, default=0.0)
    tax_delinquent = Column(Boolean, default=False)
    days_on_market = Column(Integer, default=0)
    distress_signals = Column(Text, default="[]")  # JSON list

    source = Column(String, default="manual")
    base_score = Column(Float, default=0.0)
    claude_score = Column(Float, default=0.0)
    final_score = Column(Float, default=0.0)

    status = Column(String, default="New")
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=_now)

    # --- Property characteristics (used for comps / ARV / PI sheet) -------- #
    beds = Column(Float, default=0.0)
    baths = Column(Float, default=0.0)
    sqft = Column(Float, default=0.0)
    year_built = Column(Integer, default=0)
    lot_sqft = Column(Float, default=0.0)
    property_type = Column(String, default="Single Family")
    occupancy = Column(String, default="")          # owner | tenant | vacant
    latitude = Column(Float, default=0.0)
    longitude = Column(Float, default=0.0)
    photo_urls = Column(Text, default="[]")          # JSON list of image URLs

    # --- Deal terms (set once you're working it) --------------------------- #
    arv = Column(Float, default=0.0)
    repair_estimate = Column(Float, default=0.0)
    asking_price = Column(Float, default=0.0)         # what you'll list it to buyers for
    assignment_fee_target = Column(Float, default=10000.0)
    earnest_money = Column(Float, default=0.0)
    contract_date = Column(DateTime, nullable=True)
    inspection_end_date = Column(DateTime, nullable=True)
    close_date = Column(DateTime, nullable=True)

    # --- Follow-up tracking ------------------------------------------------ #
    next_follow_up_date = Column(DateTime, nullable=True)
    last_contacted_at = Column(DateTime, nullable=True)
    contact_count = Column(Integer, default=0)

    def signals(self) -> list[str]:
        try:
            return json.loads(self.distress_signals or "[]")
        except (ValueError, TypeError):
            return []

    def photos(self) -> list[str]:
        try:
            return json.loads(self.photo_urls or "[]")
        except (ValueError, TypeError):
            return []

    def to_dict(self) -> dict:
        today = _now().date()

        def _days_until(dt):
            if not dt:
                return None
            d = dt.date() if isinstance(dt, datetime) else dt
            return (d - today).days

        return {
            "id": self.id,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "zip_code": self.zip_code,
            "county": self.county,
            "owner_name": self.owner_name,
            "owner_address": self.owner_address,
            "owner_phone": self.owner_phone,
            "owner_email": self.owner_email,
            "est_value": self.est_value,
            "est_equity_pct": self.est_equity_pct,
            "tax_delinquent": self.tax_delinquent,
            "days_on_market": self.days_on_market,
            "distress_signals": self.signals(),
            "source": self.source,
            "base_score": round(self.base_score, 1),
            "claude_score": round(self.claude_score, 1),
            "final_score": round(self.final_score, 1),
            "status": self.status,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "maps_key_present": bool(GOOGLE_MAPS_API_KEY),
            # property characteristics
            "beds": self.beds,
            "baths": self.baths,
            "sqft": self.sqft,
            "year_built": self.year_built,
            "lot_sqft": self.lot_sqft,
            "property_type": self.property_type,
            "occupancy": self.occupancy,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "photo_urls": self.photos(),
            # deal terms
            "arv": self.arv,
            "repair_estimate": self.repair_estimate,
            "asking_price": self.asking_price,
            "assignment_fee_target": self.assignment_fee_target,
            "earnest_money": self.earnest_money,
            "contract_date": self.contract_date.isoformat() if self.contract_date else None,
            "inspection_end_date": self.inspection_end_date.isoformat() if self.inspection_end_date else None,
            "close_date": self.close_date.isoformat() if self.close_date else None,
            # follow-up
            "next_follow_up_date": self.next_follow_up_date.isoformat() if self.next_follow_up_date else None,
            "last_contacted_at": self.last_contacted_at.isoformat() if self.last_contacted_at else None,
            "contact_count": self.contact_count or 0,
            # computed countdowns
            "days_until_follow_up": _days_until(self.next_follow_up_date),
            "days_to_inspection_end": _days_until(self.inspection_end_date),
            "days_to_close": _days_until(self.close_date),
        }


class Buyer(Base):
    __tablename__ = "buyers"

    id = Column(String, primary_key=True, default=_uid)
    name = Column(String, nullable=False)
    entity_type = Column(String, default="")
    email = Column(String, default="")
    phone = Column(String, default="")
    address = Column(String, default="")
    city = Column(String, default="")
    state = Column(String, default="TX")
    zip_code = Column(String, default="")
    budget_min = Column(Float, default=0.0)
    budget_max = Column(Float, default=0.0)
    preferred_areas = Column(Text, default="")
    property_types = Column(Text, default="")
    notes = Column(Text, default="")
    cash_buyer_score = Column(Float, default=50.0)
    source = Column(String, default="manual")
    created_at = Column(DateTime, default=_now)

    # --- Buy box (used for deal matching) ---------------------------------- #
    target_cities = Column(Text, default="")    # comma-separated
    target_zips = Column(Text, default="")      # comma-separated
    target_counties = Column(Text, default="")  # comma-separated
    min_beds = Column(Float, default=0.0)
    max_rehab = Column(Float, default=0.0)      # max repair budget they'll take on
    asset_types = Column(Text, default="")      # e.g. "Single Family, Duplex"
    recent_cash_deals = Column(Integer, default=0)
    active = Column(Boolean, default=True)

    # --- Proof of funds ---------------------------------------------------- #
    pof_received = Column(Boolean, default=False)
    pof_amount = Column(Float, default=0.0)
    pof_date = Column(DateTime, nullable=True)
    pof_expires = Column(DateTime, nullable=True)
    last_deal_sent_at = Column(DateTime, nullable=True)

    def _csv(self, raw) -> list[str]:
        return [s.strip() for s in (raw or "").split(",") if s.strip()]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "entity_type": self.entity_type,
            "email": self.email,
            "phone": self.phone,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "zip_code": self.zip_code,
            "budget_min": self.budget_min,
            "budget_max": self.budget_max,
            "preferred_areas": self.preferred_areas,
            "property_types": self.property_types,
            "notes": self.notes,
            "cash_buyer_score": round(self.cash_buyer_score, 1),
            "source": self.source,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "target_cities": self._csv(self.target_cities),
            "target_zips": self._csv(self.target_zips),
            "target_counties": self._csv(self.target_counties),
            "min_beds": self.min_beds,
            "max_rehab": self.max_rehab,
            "asset_types": self._csv(self.asset_types),
            "recent_cash_deals": self.recent_cash_deals or 0,
            "active": bool(self.active),
            "pof_received": bool(self.pof_received),
            "pof_amount": self.pof_amount,
            "pof_date": self.pof_date.isoformat() if self.pof_date else None,
            "pof_expires": self.pof_expires.isoformat() if self.pof_expires else None,
            "last_deal_sent_at": self.last_deal_sent_at.isoformat() if self.last_deal_sent_at else None,
        }


class Run(Base):
    __tablename__ = "runs"

    id = Column(String, primary_key=True, default=_uid)
    started_at = Column(DateTime, default=_now)
    finished_at = Column(DateTime, nullable=True)
    counties = Column(Text, default="")
    sources_used = Column(Text, default="[]")
    leads_found = Column(Integer, default=0)
    buyers_found = Column(Integer, default=0)
    status = Column(String, default="running")
    notes = Column(Text, default="")

    def to_dict(self) -> dict:
        try:
            srcs = json.loads(self.sources_used or "[]")
        except (ValueError, TypeError):
            srcs = []
        return {
            "id": self.id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "counties": self.counties,
            "sources_used": srcs,
            "leads_found": self.leads_found,
            "buyers_found": self.buyers_found,
            "status": self.status,
            "notes": self.notes,
        }


class DealLog(Base):
    __tablename__ = "deal_logs"

    id = Column(String, primary_key=True, default=_uid)
    purchase_price = Column(Float, default=0.0)
    repair = Column(Float, default=0.0)
    arv = Column(Float, default=0.0)
    closing_pct = Column(Float, default=6.0)
    assignment_fee = Column(Float, default=10000.0)
    mao = Column(Float, default=0.0)
    net_assign = Column(Float, default=0.0)
    net_list = Column(Float, default=0.0)
    margin_pct = Column(Float, default=0.0)
    created_at = Column(DateTime, default=_now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "purchase_price": self.purchase_price,
            "repair": self.repair,
            "arv": self.arv,
            "closing_pct": self.closing_pct,
            "assignment_fee": self.assignment_fee,
            "mao": round(self.mao, 2),
            "net_assign": round(self.net_assign, 2),
            "net_list": round(self.net_list, 2),
            "margin_pct": round(self.margin_pct, 2),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ContactLog(Base):
    __tablename__ = "contact_logs"

    id = Column(String, primary_key=True, default=_uid)
    lead_id = Column(String, nullable=False, default="")
    channel = Column(String, default="call")     # call | text | email | voicemail | door | other
    outcome = Column(String, default="")          # no_answer | left_vm | spoke | callback | not_interested | appointment
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=_now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "lead_id": self.lead_id,
            "channel": self.channel,
            "outcome": self.outcome,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


Base.metadata.create_all(engine)


def ensure_schema() -> None:
    """
    Lightweight forward-only migration: add any model columns that are missing
    from existing tables. create_all() only creates *new* tables, so this is how
    new columns reach a database that was created by an earlier version.
    Safe to run on every boot; it only ever ADDs missing columns.
    """
    from sqlalchemy import inspect as _sa_inspect, text as _sa_text

    dialect = engine.dialect.name  # 'sqlite' | 'postgresql'

    def sql_type(col) -> str:
        t = col.type.__class__.__name__.lower()
        if "boolean" in t:
            return "BOOLEAN"
        if "integer" in t:
            return "INTEGER"
        if "float" in t or "numeric" in t:
            return "DOUBLE PRECISION" if dialect == "postgresql" else "FLOAT"
        if "datetime" in t:
            return "TIMESTAMP"
        return "TEXT"

    insp = _sa_inspect(engine)
    existing_tables = set(insp.get_table_names())

    with engine.begin() as conn:
        for table_name, model in Base.metadata.tables.items():
            if table_name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table_name)}
            for col in model.columns:
                if col.name in have:
                    continue
                ddl = f'ALTER TABLE {table_name} ADD COLUMN {col.name} {sql_type(col)}'
                try:
                    conn.execute(_sa_text(ddl))
                    print(f"[migrate] {table_name}.{col.name} added")
                except Exception as exc:  # already exists / race / dialect quirk
                    print(f"[migrate] skip {table_name}.{col.name}: {exc}")


ensure_schema()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Pydantic schemas
# --------------------------------------------------------------------------- #

class ManualBuyer(BaseModel):
    name: str
    entity_type: str = ""
    email: str = ""
    phone: str = ""
    address: str = ""
    city: str = ""
    state: str = "TX"
    zip: str = ""
    budget_min: float = 0.0
    budget_max: float = 0.0
    preferred_areas: str = ""
    property_types: str = ""
    notes: str = ""

    @field_validator("name")
    @classmethod
    def name_required(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name is required")
        return v.strip()


class ManualLead(BaseModel):
    address: str
    city: str = ""
    state: str = "TX"
    zip: str = ""
    county: str = ""
    owner_name: str = ""
    owner_phone: str = ""
    owner_email: str = ""
    est_value: float = 0.0
    est_equity_pct: float = 0.0
    tax_delinquent: bool = False
    days_on_market: int = 0
    distress_signals: list[str] = Field(default_factory=list)
    notes: str = ""


class StatusUpdate(BaseModel):
    status: str


class DealInput(BaseModel):
    purchase_price: float = 0.0
    repair: float = 0.0
    arv: float = 0.0
    closing_pct: float = 6.0
    assignment_fee: float = 10000.0


class OfferLetterInput(BaseModel):
    property_address: str
    seller_name: str
    purchase_price: float
    earnest_money: float = 1000.0
    closing_date: str = ""
    buyer_entity: str = ""


class AssignmentContractInput(BaseModel):
    original_contract_price: float
    assignment_fee: float
    seller_address: str
    assignee_name: str
    assignee_entity: str = ""
    closing_date: str = ""


class TemplateInput(BaseModel):
    type: str  # seller_letter | call_script | buyer_text
    context: dict = Field(default_factory=dict)


class AddressInput(BaseModel):
    address: str
    city: str = ""
    state: str = "TX"
    zip: str = ""


class SkipTraceInput(BaseModel):
    lead_ids: list[str]


class RunInput(BaseModel):
    counties: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Scoring engine
# --------------------------------------------------------------------------- #

def rule_based_lead_score(record: dict) -> tuple[float, list[str]]:
    """Deterministic 0-100 motivation score with human-readable reasons."""
    score = 0.0
    reasons: list[str] = []

    equity = float(record.get("est_equity_pct", 0) or 0)
    if equity >= 60:
        score += 30
        reasons.append(f"High equity ({equity:.0f}%)")
    elif equity >= 35:
        score += 18
        reasons.append(f"Moderate equity ({equity:.0f}%)")
    elif equity > 0:
        score += 8

    if record.get("tax_delinquent"):
        score += 22
        reasons.append("Tax delinquent")

    signals = record.get("distress_signals") or []
    if isinstance(signals, str):
        try:
            signals = json.loads(signals)
        except (ValueError, TypeError):
            signals = [signals]
    weight = {
        "foreclosure": 20,
        "pre-foreclosure": 16,
        "probate": 14,
        "code-violation": 10,
        "vacant": 12,
        "absentee-owner": 9,
        "fsbo": 6,
        "expired-listing": 8,
        "divorce": 10,
        "bankruptcy": 12,
        "auction": 14,
        "liens": 10,
    }
    for sig in signals:
        key = str(sig).strip().lower()
        if key in weight:
            score += weight[key]
            reasons.append(sig)

    dom = int(record.get("days_on_market", 0) or 0)
    if dom >= 120:
        score += 12
        reasons.append(f"{dom} days on market")
    elif dom >= 60:
        score += 6

    if not reasons:
        reasons.append("Baseline lead, limited distress data")

    return min(round(score, 1), 100.0), reasons


def cash_buyer_score(record: dict) -> float:
    score = 50.0
    if record.get("entity_type", "").lower() in {"llc", "lp", "inc", "corp", "trust"}:
        score += 12
    deals = int(record.get("recent_cash_deals", 0) or 0)
    score += min(deals * 4, 24)
    if float(record.get("budget_max", 0) or 0) > 0:
        score += 6
    return min(round(score, 1), 100.0)


def claude_enrich_leads(leads: list[Lead]) -> int:
    """
    Optional Claude scoring on the top 50 leads (60% base / 40% Claude blend).
    No-op (returns 0) when ANTHROPIC_API_KEY is unset or the SDK call fails.
    """
    if not ANTHROPIC_API_KEY or not leads:
        return 0
    try:
        import anthropic  # imported lazily; optional dependency path
    except ImportError:
        return 0

    top = sorted(leads, key=lambda x: x.base_score, reverse=True)[:50]
    payload = [
        {
            "id": l.id,
            "address": l.address,
            "city": l.city,
            "equity_pct": l.est_equity_pct,
            "tax_delinquent": l.tax_delinquent,
            "days_on_market": l.days_on_market,
            "signals": l.signals(),
            "base_score": l.base_score,
        }
        for l in top
    ]
    prompt = (
        "You are a real estate acquisitions analyst. Score each lead 0-100 for "
        "seller motivation and wholesale fit. Respond ONLY with a JSON array of "
        '{"id": "...", "score": <0-100>} objects, no prose, no markdown.\n\n'
        f"Leads:\n{json.dumps(payload)}"
    )
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        text = text.replace("```json", "").replace("```", "").strip()
        scored = {row["id"]: float(row["score"]) for row in json.loads(text)}
    except Exception as exc:  # network, parse, auth — all non-fatal
        print(f"[claude] enrichment skipped: {exc}")
        return 0

    updated = 0
    by_id = {l.id: l for l in top}
    for lid, cscore in scored.items():
        lead = by_id.get(lid)
        if lead:
            lead.claude_score = min(max(cscore, 0.0), 100.0)
            lead.final_score = round(0.6 * lead.base_score + 0.4 * lead.claude_score, 1)
            updated += 1
    return updated


# --------------------------------------------------------------------------- #
# Generators (offer letter, assignment contract, outreach templates)
# --------------------------------------------------------------------------- #

def _money(v: float) -> str:
    return f"${v:,.2f}"


def build_offer_letter(d: OfferLetterInput) -> str:
    closing = d.closing_date or "to be agreed (target 21-30 days)"
    entity = d.buyer_entity or "[Buyer / Assignee]"
    return f"""LETTER OF INTENT TO PURCHASE REAL PROPERTY

Date: {datetime.now().strftime("%B %d, %Y")}

To: {d.seller_name}
Re: {d.property_address}

Dear {d.seller_name},

This Letter of Intent outlines the principal terms under which {entity}
("Buyer") proposes to purchase the property located at {d.property_address}
("Property"). This letter is non-binding and is intended to serve as the basis
for a formal purchase agreement.

  1. Purchase Price:   {_money(d.purchase_price)}
  2. Earnest Money:    {_money(d.earnest_money)}, deposited with a Texas title
                       company within 3 business days of a signed agreement.
  3. Closing Date:     {closing}.
  4. Title & Closing:  Closing at a mutually agreed Texas title company. Seller
                       to convey marketable title by general warranty deed.
  5. Inspection:       Buyer may inspect the Property during an option period.
  6. As-Is:            Property purchased in its present, as-is condition.

DISCLOSURE (Texas Occupations Code Sec. 1101.0045): Buyer may assign this
contract or the resulting purchase agreement to a third party, and Buyer is
acquiring an equitable interest in the Property rather than acting as a licensed
real estate broker on Seller's behalf. Buyer is not a licensed real estate
broker or sales agent unless separately disclosed in writing.

This Letter of Intent is non-binding except as to any confidentiality terms and
does not create an obligation to purchase or sell. A binding obligation arises
only upon execution of a definitive written purchase agreement by both parties.

Sincerely,

____________________________
{entity}, Buyer

Acknowledged by Seller:

____________________________        Date: ____________
{d.seller_name}
"""


def build_assignment_contract(d: AssignmentContractInput) -> str:
    closing = d.closing_date or "[Closing Date]"
    assignee = d.assignee_name
    if d.assignee_entity:
        assignee = f"{d.assignee_name} ({d.assignee_entity})"
    total = d.original_contract_price + d.assignment_fee
    return f"""ASSIGNMENT OF REAL ESTATE PURCHASE CONTRACT
(State of Texas)

Date: {datetime.now().strftime("%B %d, %Y")}

This Assignment of Real Estate Purchase Contract ("Assignment") is made between
the undersigned Assignor and Assignee.

PROPERTY: {d.seller_address}

RECITALS
A. Assignor holds an equitable interest as buyer under a certain Purchase
   Agreement for the Property (the "Original Contract") at a purchase price of
   {_money(d.original_contract_price)}.
B. Assignor wishes to assign all of its right, title, and interest in the
   Original Contract to Assignee.

TERMS
  1. Assignment. Assignor assigns to {assignee} ("Assignee") all rights and
     obligations under the Original Contract.
  2. Assignment Fee. Assignee shall pay Assignor a non-refundable assignment
     fee of {_money(d.assignment_fee)}, due at closing through the title company.
  3. Total to Close. Assignee's total acquisition cost is approximately
     {_money(total)} (original price plus assignment fee), exclusive of closing
     costs.
  4. Closing. Closing shall occur on or before {closing} at the title company
     named in the Original Contract.
  5. Assignee Acceptance. Assignee accepts and agrees to perform all obligations
     of the buyer under the Original Contract.

DISCLOSURE (Texas Occupations Code Sec. 1101.0045): The Assignor is selling its
equitable interest in a real estate contract, not the real property itself, and
is not acting as a licensed real estate broker. Assignor has disclosed to the
seller that it intends to assign or profit from the assignment of the Original
Contract.

ASSIGNOR: ____________________________   Date: __________

ASSIGNEE: ____________________________   Date: __________
{assignee}

NOTE: This is a working draft, not legal advice. Texas assignment practice
carries specific disclosure and licensing rules — have a Texas real estate
attorney review before use.
"""


STATIC_TEMPLATES = {
    "seller_letter": (
        "Hi {owner_name},\n\n"
        "My name is {investor_name} and I'm a local buyer interested in the "
        "property at {address}. I buy homes directly, in any condition, and can "
        "close quickly on your timeline with no agent commissions or repairs "
        "needed on your end.\n\n"
        "If you've ever thought about selling, I'd love to make you a fair, "
        "no-obligation cash offer. You can reach me anytime at {phone} or "
        "{email}.\n\n"
        "Thanks for your time,\n{investor_name}"
    ),
    "call_script": (
        "Intro: Hi, is this {owner_name}? My name's {investor_name} — I'm a "
        "local home buyer here in {city}. Do you have 30 seconds?\n\n"
        "Hook: I came across your property at {address} and wanted to ask — "
        "have you given any thought to selling it?\n\n"
        "Discovery:\n"
        "  - What's the condition like right now?\n"
        "  - Is it occupied, rented, or vacant?\n"
        "  - If you did sell, what would need to happen for it to be a win?\n\n"
        "Offer framing: I buy as-is and cover closing costs, so there are no "
        "agent fees or repairs on your side. If the numbers work, I can close on "
        "your timeline.\n\n"
        "Close: Can I take a quick look and follow up with a no-obligation cash "
        "number by {followup_day}?"
    ),
    "buyer_text": (
        "Hey {buyer_name}, new deal in {city}: {address}. ARV ~{arv}, est. "
        "repairs ~{repairs}, asking {price} assigned. Cash/quick close. Want the "
        "full packet? Reply YES and I'll send comps + photos."
    ),
}


def render_template(t: TemplateInput) -> dict:
    ctx = {k: str(v) for k, v in (t.context or {}).items()}
    fallback = {
        "owner_name": "[owner]",
        "investor_name": "[your name]",
        "address": "[property address]",
        "phone": "[your phone]",
        "email": "[your email]",
        "city": "[city]",
        "followup_day": "Friday",
        "buyer_name": "[buyer]",
        "arv": "[ARV]",
        "repairs": "[repairs]",
        "price": "[price]",
    }
    fallback.update(ctx)

    if t.type not in STATIC_TEMPLATES:
        raise HTTPException(400, f"Unknown template type: {t.type}")

    # Try Claude for a tailored version; fall back to the static template.
    if ANTHROPIC_API_KEY:
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            label = t.type.replace("_", " ")
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=600,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Write a concise, friendly, professional real estate "
                            f"wholesaling {label}. Use these details: "
                            f"{json.dumps(fallback)}. Plain text only, no markdown."
                        ),
                    }
                ],
            )
            text = "".join(
                b.text for b in resp.content if getattr(b, "type", "") == "text"
            ).strip()
            if text:
                return {"type": t.type, "source": "claude", "text": text}
        except Exception as exc:
            print(f"[claude] template fallback: {exc}")

    text = STATIC_TEMPLATES[t.type].format(**fallback)
    return {"type": t.type, "source": "static", "text": text}


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #

app = FastAPI(title=APP_NAME, version=APP_VERSION)


@app.get("/api/health")
def health():
    enabled = source_engine.enabled_source_names()
    return {
        "status": "ok",
        "app": APP_NAME,
        "version": APP_VERSION,
        "time": _now().isoformat(),
        "demo_mode": DEMO_MODE,
        "database": "postgres" if DATABASE_URL.startswith("postgresql") else "sqlite",
        "integrations": {
            "anthropic": bool(ANTHROPIC_API_KEY),
            "attom": bool(os.getenv("ATTOM_API_KEY")),
            "usps": bool(USPS_USER_ID),
            "google_maps": bool(GOOGLE_MAPS_API_KEY),
            "batchdata": bool(BATCHDATA_API_KEY),
            "tracerfy": bool(TRACERFY_API_KEY),
        },
        "enabled_sources": enabled,
    }


# ----- Leads --------------------------------------------------------------- #

@app.get("/api/leads")
def list_leads(
    status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    stmt = select(Lead)
    if status:
        stmt = stmt.where(Lead.status == status)
    stmt = stmt.order_by(Lead.final_score.desc(), Lead.created_at.desc())
    leads = db.execute(stmt).scalars().all()
    return {"count": len(leads), "leads": [l.to_dict() for l in leads]}


@app.get("/api/leads/{lead_id}")
def get_lead(lead_id: str, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return lead.to_dict()


@app.post("/api/leads/manual")
def create_lead(payload: ManualLead, db: Session = Depends(get_db)):
    record = payload.model_dump()
    base, reasons = rule_based_lead_score(
        {**record, "distress_signals": payload.distress_signals}
    )
    lead = Lead(
        address=payload.address,
        city=payload.city,
        state=payload.state,
        zip_code=payload.zip,
        county=payload.county,
        owner_name=payload.owner_name,
        owner_phone=payload.owner_phone,
        owner_email=payload.owner_email,
        est_value=payload.est_value,
        est_equity_pct=payload.est_equity_pct,
        tax_delinquent=payload.tax_delinquent,
        days_on_market=payload.days_on_market,
        distress_signals=json.dumps(payload.distress_signals or reasons),
        source="manual",
        base_score=base,
        final_score=base,
        notes=payload.notes,
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead.to_dict()


@app.put("/api/leads/{lead_id}/status")
def update_status(lead_id: str, payload: StatusUpdate, db: Session = Depends(get_db)):
    if payload.status not in PIPELINE_STAGES:
        raise HTTPException(400, f"Invalid status. Use one of: {PIPELINE_STAGES}")
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    lead.status = payload.status
    db.commit()
    db.refresh(lead)
    return lead.to_dict()


# ----- Buyers -------------------------------------------------------------- #

@app.get("/api/buyers")
def list_buyers(db: Session = Depends(get_db)):
    stmt = select(Buyer).order_by(Buyer.cash_buyer_score.desc(), Buyer.created_at.desc())
    buyers = db.execute(stmt).scalars().all()
    return {"count": len(buyers), "buyers": [b.to_dict() for b in buyers]}


@app.post("/api/buyers/manual")
def create_buyer(payload: ManualBuyer, db: Session = Depends(get_db)):
    if not (payload.email.strip() or payload.phone.strip()):
        raise HTTPException(400, "Provide at least one of email or phone")
    buyer = Buyer(
        name=payload.name,
        entity_type=payload.entity_type,
        email=payload.email,
        phone=payload.phone,
        address=payload.address,
        city=payload.city,
        state=payload.state,
        zip_code=payload.zip,
        budget_min=payload.budget_min,
        budget_max=payload.budget_max,
        preferred_areas=payload.preferred_areas,
        property_types=payload.property_types,
        notes=payload.notes,
        cash_buyer_score=cash_buyer_score(payload.model_dump()),
        source="manual",
    )
    db.add(buyer)
    db.commit()
    db.refresh(buyer)
    return buyer.to_dict()


# ----- CSV import ---------------------------------------------------------- #

async def _read_csv(request: Request) -> list[dict]:
    raw = await request.body()
    if not raw:
        raise HTTPException(400, "Empty body. POST raw CSV text.")
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()} for row in reader]
    if not rows:
        raise HTTPException(400, "No rows parsed from CSV")
    return rows


def _to_float(v, default=0.0) -> float:
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return default


@app.post("/api/import/buyers")
async def import_buyers(request: Request, db: Session = Depends(get_db)):
    rows = await _read_csv(request)
    created = 0
    for r in rows:
        name = r.get("name") or r.get("buyer_name") or r.get("company")
        if not name:
            continue
        if not (r.get("email") or r.get("phone")):
            continue
        buyer = Buyer(
            name=name,
            entity_type=r.get("entity_type", ""),
            email=r.get("email", ""),
            phone=r.get("phone", ""),
            address=r.get("address", ""),
            city=r.get("city", ""),
            state=r.get("state", "TX"),
            zip_code=r.get("zip", "") or r.get("zip_code", ""),
            budget_min=_to_float(r.get("budget_min")),
            budget_max=_to_float(r.get("budget_max")),
            preferred_areas=r.get("preferred_areas", ""),
            property_types=r.get("property_types", ""),
            notes=r.get("notes", ""),
            cash_buyer_score=cash_buyer_score(r),
            source="csv_import",
        )
        db.add(buyer)
        created += 1
    db.commit()
    return {"imported": created, "skipped": len(rows) - created}


@app.post("/api/import/properties")
async def import_properties(request: Request, db: Session = Depends(get_db)):
    rows = await _read_csv(request)
    created = 0
    for r in rows:
        address = r.get("address") or r.get("property_address")
        if not address:
            continue
        signals_raw = r.get("distress_signals", "")
        signals = [s.strip() for s in signals_raw.split(";") if s.strip()] if signals_raw else []
        record = {
            "est_equity_pct": _to_float(r.get("est_equity_pct")),
            "tax_delinquent": str(r.get("tax_delinquent", "")).lower() in {"1", "true", "yes", "y"},
            "days_on_market": int(_to_float(r.get("days_on_market"))),
            "distress_signals": signals,
        }
        base, reasons = rule_based_lead_score(record)
        lead = Lead(
            address=address,
            city=r.get("city", ""),
            state=r.get("state", "TX"),
            zip_code=r.get("zip", "") or r.get("zip_code", ""),
            county=r.get("county", ""),
            owner_name=r.get("owner_name", ""),
            owner_address=r.get("owner_address", ""),
            est_value=_to_float(r.get("est_value")),
            est_equity_pct=record["est_equity_pct"],
            tax_delinquent=record["tax_delinquent"],
            days_on_market=record["days_on_market"],
            distress_signals=json.dumps(signals or reasons),
            source="csv_import",
            base_score=base,
            final_score=base,
        )
        db.add(lead)
        created += 1
    db.commit()
    return {"imported": created, "skipped": len(rows) - created}


# ----- CSV export ---------------------------------------------------------- #

@app.get("/api/export/top-leads")
def export_top_leads(db: Session = Depends(get_db)):
    stmt = select(Lead).order_by(Lead.final_score.desc()).limit(50)
    leads = db.execute(stmt).scalars().all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "address", "city", "state", "zip_code", "final_score", "base_score",
        "claude_score", "distress_signals", "source", "owner_name", "owner_address",
        "est_value", "est_equity_pct", "tax_delinquent", "days_on_market", "created_at",
    ])
    for l in leads:
        writer.writerow([
            l.id, l.address, l.city, l.state, l.zip_code, round(l.final_score, 1),
            round(l.base_score, 1), round(l.claude_score, 1), "; ".join(l.signals()),
            l.source, l.owner_name, l.owner_address, l.est_value, l.est_equity_pct,
            l.tax_delinquent, l.days_on_market,
            l.created_at.isoformat() if l.created_at else "",
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=top-leads.csv"},
    )


# ----- Deal calculator ----------------------------------------------------- #

def compute_deal(d: DealInput) -> dict:
    closing_costs = d.arv * (d.closing_pct / 100.0)
    # 70% rule MAO, net of repairs and the wholesaler's assignment fee.
    mao = (d.arv * 0.70) - d.repair - d.assignment_fee
    net_assign = d.assignment_fee
    net_list = d.arv - d.purchase_price - d.repair - closing_costs
    margin_pct = (net_list / d.arv * 100.0) if d.arv else 0.0
    cash_buyer_target = d.purchase_price + d.assignment_fee
    if margin_pct >= 15:
        flag = "green"
    elif margin_pct >= 8:
        flag = "yellow"
    else:
        flag = "red"
    return {
        "mao": round(mao, 2),
        "net_assign": round(net_assign, 2),
        "net_list": round(net_list, 2),
        "cash_buyer_target": round(cash_buyer_target, 2),
        "closing_costs": round(closing_costs, 2),
        "margin_pct": round(margin_pct, 2),
        "margin_flag": flag,
    }


@app.post("/api/calculate-deal")
def calculate_deal(d: DealInput, db: Session = Depends(get_db)):
    result = compute_deal(d)
    log = DealLog(
        purchase_price=d.purchase_price,
        repair=d.repair,
        arv=d.arv,
        closing_pct=d.closing_pct,
        assignment_fee=d.assignment_fee,
        mao=result["mao"],
        net_assign=result["net_assign"],
        net_list=result["net_list"],
        margin_pct=result["margin_pct"],
    )
    db.add(log)
    db.commit()
    return {"input": d.model_dump(), **result}


# ----- Generators ---------------------------------------------------------- #

@app.post("/api/generate/offer-letter")
def gen_offer_letter(d: OfferLetterInput):
    return {"letter": build_offer_letter(d)}


@app.post("/api/generate/assignment-contract")
def gen_assignment_contract(d: AssignmentContractInput):
    return {"contract": build_assignment_contract(d)}


@app.post("/api/generate/template")
def gen_template(d: TemplateInput):
    return render_template(d)


# ----- USPS address validation --------------------------------------------- #

@app.post("/api/validate-address")
def validate_address(d: AddressInput):
    if not USPS_USER_ID:
        return {
            "validated": False,
            "note": "USPS_USER_ID not configured. Returning input unchanged.",
            "address": d.model_dump(),
        }
    xml = (
        f'<AddressValidateRequest USERID="{USPS_USER_ID}">'
        f"<Revision>1</Revision>"
        f'<Address ID="0">'
        f"<Address1></Address1>"
        f"<Address2>{d.address}</Address2>"
        f"<City>{d.city}</City>"
        f"<State>{d.state}</State>"
        f"<Zip5>{d.zip}</Zip5>"
        f"<Zip4></Zip4>"
        f"</Address></AddressValidateRequest>"
    )
    url = "https://secure.shippingapis.com/ShippingAPI.dll"
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.get(url, params={"API": "Verify", "XML": xml})
        body = resp.text
        if "<Error>" in body:
            return {"validated": False, "note": "USPS returned an error", "raw": body[:500]}
        return {"validated": True, "raw": body}
    except httpx.HTTPError as exc:
        return {"validated": False, "note": f"USPS request failed: {exc}", "address": d.model_dump()}


# ----- Skip trace ---------------------------------------------------------- #

@app.post("/api/skip-trace/batch")
def skip_trace_batch(d: SkipTraceInput, db: Session = Depends(get_db)):
    leads = [db.get(Lead, lid) for lid in d.lead_ids]
    leads = [l for l in leads if l]
    if not leads:
        raise HTTPException(404, "No matching leads found")

    rows = [
        {
            "lead_id": l.id,
            "owner_name": l.owner_name,
            "address": l.address,
            "city": l.city,
            "state": l.state,
            "zip": l.zip_code,
        }
        for l in leads
    ]

    provider = None
    if BATCHDATA_API_KEY:
        provider = "batchdata"
    elif TRACERFY_API_KEY:
        provider = "tracerfy"

    if not provider:
        return {
            "provider": None,
            "note": "No skip-trace key set (BATCHDATA_API_KEY / TRACERFY_API_KEY). "
            "Returning a CSV-ready batch you can run manually.",
            "batch": rows,
        }

    # Live provider call. Endpoints intentionally generic; confirm against your
    # provider's current API docs before relying on production output.
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            if provider == "batchdata":
                resp = client.post(
                    "https://api.batchdata.com/api/v1/property/skip-trace",
                    headers={"Authorization": f"Bearer {BATCHDATA_API_KEY}"},
                    json={"requests": rows},
                )
            else:
                resp = client.post(
                    "https://api.tracerfy.com/v1/skip-trace/batch",
                    headers={"Authorization": f"Bearer {TRACERFY_API_KEY}"},
                    json={"records": rows},
                )
        return {"provider": provider, "status_code": resp.status_code, "result": resp.json()}
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "provider": provider,
            "note": f"Provider request failed ({exc}). Returning manual batch.",
            "batch": rows,
        }


# ----- Market stats -------------------------------------------------------- #

STATIC_MARKET_STATS = {
    "Dallas": {"median_price": 410000, "dom": 38, "yoy_pct": 2.1, "inventory_mo": 2.9},
    "Houston": {"median_price": 345000, "dom": 41, "yoy_pct": 1.4, "inventory_mo": 3.4},
    "San Antonio": {"median_price": 305000, "dom": 47, "yoy_pct": 0.8, "inventory_mo": 3.8},
    "Austin": {"median_price": 545000, "dom": 52, "yoy_pct": -1.9, "inventory_mo": 4.2},
    "Fort Worth": {"median_price": 355000, "dom": 36, "yoy_pct": 2.6, "inventory_mo": 2.7},
}


@app.get("/api/market-stats")
def market_stats():
    attom_key = os.getenv("ATTOM_API_KEY", "").strip()
    if attom_key:
        live = source_engine.fetch_market_stats(attom_key, list(STATIC_MARKET_STATS.keys()))
        if live:
            return {"source": "attom", "markets": live}
    return {"source": "static_fallback", "markets": STATIC_MARKET_STATS}


# ----- Runs (source ingestion) --------------------------------------------- #

@app.get("/api/runs")
def list_runs(db: Session = Depends(get_db)):
    stmt = select(Run).order_by(Run.started_at.desc()).limit(50)
    runs = db.execute(stmt).scalars().all()
    return {"count": len(runs), "runs": [r.to_dict() for r in runs]}


@app.post("/api/runs")
def trigger_run(payload: RunInput, db: Session = Depends(get_db)):
    counties = payload.counties or source_engine.DEFAULT_COUNTIES
    run = Run(counties=", ".join(counties), status="running")
    db.add(run)
    db.commit()
    db.refresh(run)

    used: list[str] = []
    lead_count = 0
    buyer_count = 0

    # Seller/deal sources
    for raw_lead, src_name in source_engine.collect_leads(counties, demo=DEMO_MODE):
        base, reasons = rule_based_lead_score(raw_lead)
        lead = Lead(
            address=raw_lead.get("address", ""),
            city=raw_lead.get("city", ""),
            state=raw_lead.get("state", "TX"),
            zip_code=raw_lead.get("zip_code", ""),
            county=raw_lead.get("county", ""),
            owner_name=raw_lead.get("owner_name", ""),
            owner_address=raw_lead.get("owner_address", ""),
            owner_phone=raw_lead.get("owner_phone", ""),
            owner_email=raw_lead.get("owner_email", ""),
            est_value=raw_lead.get("est_value", 0.0),
            est_equity_pct=raw_lead.get("est_equity_pct", 0.0),
            tax_delinquent=raw_lead.get("tax_delinquent", False),
            days_on_market=raw_lead.get("days_on_market", 0),
            distress_signals=json.dumps(raw_lead.get("distress_signals") or reasons),
            beds=raw_lead.get("beds", 0.0),
            baths=raw_lead.get("baths", 0.0),
            sqft=raw_lead.get("sqft", 0.0),
            year_built=raw_lead.get("year_built", 0),
            lot_sqft=raw_lead.get("lot_sqft", 0.0),
            property_type=raw_lead.get("property_type", "Single Family"),
            occupancy=raw_lead.get("occupancy", ""),
            latitude=raw_lead.get("latitude", 0.0),
            longitude=raw_lead.get("longitude", 0.0),
            source=src_name,
            base_score=base,
            final_score=base,
        )
        db.add(lead)
        lead_count += 1
        if src_name not in used:
            used.append(src_name)
    db.commit()

    # Buyer sources
    for raw_buyer, src_name in source_engine.collect_buyers(counties, demo=DEMO_MODE):
        buyer = Buyer(
            name=raw_buyer.get("name", "Unknown"),
            entity_type=raw_buyer.get("entity_type", ""),
            email=raw_buyer.get("email", ""),
            phone=raw_buyer.get("phone", ""),
            city=raw_buyer.get("city", ""),
            state=raw_buyer.get("state", "TX"),
            budget_min=raw_buyer.get("budget_min", 0.0),
            budget_max=raw_buyer.get("budget_max", 0.0),
            target_cities=raw_buyer.get("target_cities", ""),
            target_zips=raw_buyer.get("target_zips", ""),
            target_counties=raw_buyer.get("target_counties", ""),
            min_beds=raw_buyer.get("min_beds", 0.0),
            max_rehab=raw_buyer.get("max_rehab", 0.0),
            asset_types=raw_buyer.get("asset_types", ""),
            recent_cash_deals=raw_buyer.get("recent_cash_deals", 0),
            pof_received=raw_buyer.get("pof_received", False),
            pof_amount=raw_buyer.get("pof_amount", 0.0),
            cash_buyer_score=cash_buyer_score(raw_buyer),
            source=src_name,
        )
        db.add(buyer)
        buyer_count += 1
        if src_name not in used:
            used.append(src_name)
    db.commit()

    # Optional Claude enrichment on the top leads
    fresh = db.execute(select(Lead).order_by(Lead.base_score.desc()).limit(50)).scalars().all()
    enriched = claude_enrich_leads(fresh)
    if enriched:
        db.commit()

    run.finished_at = _now()
    run.sources_used = json.dumps(used)
    run.leads_found = lead_count
    run.buyers_found = buyer_count
    run.status = "complete"
    run.notes = (
        f"{enriched} leads Claude-enriched. "
        if enriched
        else "Claude enrichment skipped (no key or no leads). "
    )
    if not used:
        run.notes += "No sources enabled — set ENABLE_* env vars or DEMO_MODE=true."
    db.commit()
    db.refresh(run)
    return run.to_dict()


@app.get("/api/stats")
def dashboard_stats(db: Session = Depends(get_db)):
    total_leads = db.execute(select(func.count(Lead.id))).scalar() or 0
    total_buyers = db.execute(select(func.count(Buyer.id))).scalar() or 0
    hot = db.execute(select(func.count(Lead.id)).where(Lead.final_score >= 70)).scalar() or 0
    by_status = {}
    for stage in PIPELINE_STAGES:
        by_status[stage] = db.execute(
            select(func.count(Lead.id)).where(Lead.status == stage)
        ).scalar() or 0

    # Follow-ups due today/overdue + deadlines within 7 days.
    now = _now()
    followups_due = 0
    deadlines_week = 0
    for l in db.execute(select(Lead)).scalars().all():
        if l.next_follow_up_date:
            due = l.next_follow_up_date
            due = due.replace(tzinfo=timezone.utc) if due.tzinfo is None else due
            if (due.date() - now.date()).days <= 0:
                followups_due += 1
        for dt in (l.inspection_end_date, l.close_date):
            if dt:
                d = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
                if 0 <= (d.date() - now.date()).days <= 7:
                    deadlines_week += 1
                    break

    return {
        "total_leads": total_leads,
        "total_buyers": total_buyers,
        "hot_leads": hot,
        "pipeline": by_status,
        "follow_ups_due": followups_due,
        "deadlines_this_week": deadlines_week,
        "config": {
            "maps_key_present": bool(GOOGLE_MAPS_API_KEY),
            "claude_present": bool(ANTHROPIC_API_KEY),
        },
    }


# --------------------------------------------------------------------------- #
# Dashboard (static SPA-style pages)
# --------------------------------------------------------------------------- #

@app.get("/", response_class=HTMLResponse)
def root():
    index = os.path.join(DASHBOARD_DIR, "index.html")
    if os.path.exists(index):
        with open(index, encoding="utf-8") as fh:
            return HTMLResponse(fh.read())
    return HTMLResponse("<h1>TexWholesale Engine</h1><p>Dashboard files missing.</p>")


@app.get("/config.js", response_class=PlainTextResponse)
def config_js():
    """Expose only the public, non-secret presence flag for Google Maps."""
    key = GOOGLE_MAPS_API_KEY
    return PlainTextResponse(
        f"window.APP_CONFIG = {{ mapsKey: {json.dumps(key)} }};",
        media_type="application/javascript",
    )


# ===========================================================================
# WHOLESALING WORKFLOW — comps/ARV, follow-ups, buyer matching, PI sheet,
# deal terms & deadline tracking. (Added v1.1.0)
# ===========================================================================

# ----- Schemas ------------------------------------------------------------- #

class CompInput(BaseModel):
    address: str = ""
    sale_price: float = 0.0
    sqft: float = 0.0
    sale_date: str = ""
    beds: float = 0.0
    baths: float = 0.0
    year_built: int = 0
    distance_mi: float = 0.0


class CompsRequest(BaseModel):
    lead_id: str = ""
    subject_sqft: float = 0.0
    subject_beds: float = 0.0
    subject_baths: float = 0.0
    property_type: str = ""
    postal_code: str = ""
    city: str = ""
    state: str = "TX"
    latitude: float = 0.0
    longitude: float = 0.0
    radius_mi: float = 1.0
    months_back: int = 6
    sqft_tolerance_pct: float = 25.0     # comps within +/- this % of subject GLA
    repair_estimate: float = 0.0
    comps: list[CompInput] = Field(default_factory=list)   # manual comps (optional)
    save: bool = False                    # write ARV/repair back to the lead


class ContactLogInput(BaseModel):
    channel: str = "call"
    outcome: str = ""
    notes: str = ""
    next_follow_up_in_days: int | None = None
    next_follow_up_date: str | None = None


class DealTermsInput(BaseModel):
    arv: float | None = None
    repair_estimate: float | None = None
    asking_price: float | None = None
    assignment_fee_target: float | None = None
    earnest_money: float | None = None
    beds: float | None = None
    baths: float | None = None
    sqft: float | None = None
    year_built: int | None = None
    lot_sqft: float | None = None
    property_type: str | None = None
    occupancy: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    photo_urls: list[str] | None = None
    contract_date: str | None = None
    inspection_end_date: str | None = None
    close_date: str | None = None


class BuyerUpdate(BaseModel):
    name: str | None = None
    entity_type: str | None = None
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    state: str | None = None
    budget_min: float | None = None
    budget_max: float | None = None
    target_cities: str | None = None
    target_zips: str | None = None
    target_counties: str | None = None
    min_beds: float | None = None
    max_rehab: float | None = None
    asset_types: str | None = None
    recent_cash_deals: int | None = None
    active: bool | None = None
    notes: str | None = None


class PofInput(BaseModel):
    pof_received: bool = True
    pof_amount: float = 0.0
    pof_date: str | None = None
    pof_expires: str | None = None


# ----- Date helpers -------------------------------------------------------- #

def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    txt = str(s).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(txt[:len(fmt) + 6] if "%f" in fmt else txt[:len(fmt)], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(txt.replace("Z", "+00:00"))
    except ValueError:
        return None


# ----- ARV from comparable sales ------------------------------------------- #

def compute_arv(
    subject_sqft: float,
    comps: list[dict],
    *,
    sqft_tolerance_pct: float = 25.0,
    trim_outliers: bool = True,
) -> dict:
    """
    Quick-and-defensible ARV: take qualified sold comps, compute $/sqft for each,
    use the median $/sqft applied to the subject's GLA. Returns ARV plus a range,
    a confidence rating, and the per-comp breakdown so the number is auditable.
    """
    qualified: list[dict] = []
    excluded: list[dict] = []
    lo = subject_sqft * (1 - sqft_tolerance_pct / 100.0) if subject_sqft else 0
    hi = subject_sqft * (1 + sqft_tolerance_pct / 100.0) if subject_sqft else 0

    for c in comps:
        price = float(c.get("sale_price") or 0)
        sqft = float(c.get("sqft") or 0)
        if price <= 0 or sqft <= 0:
            excluded.append({**c, "reason": "missing price or sqft"})
            continue
        if subject_sqft and not (lo <= sqft <= hi):
            excluded.append({**c, "reason": f"sqft {sqft:.0f} outside ±{sqft_tolerance_pct:.0f}% band"})
            continue
        ppsf = price / sqft
        qualified.append({**c, "ppsf": round(ppsf, 2)})

    if not qualified:
        return {
            "arv": 0.0, "arv_low": 0.0, "arv_high": 0.0,
            "ppsf_median": 0.0, "confidence": "none",
            "qualified_count": 0, "comps_used": [], "comps_excluded": excluded,
            "note": "No comps met the filters — widen the sqft tolerance, radius, or date window, or enter comps manually.",
        }

    ppsfs = sorted(c["ppsf"] for c in qualified)

    # Optional light outlier trim (drop top & bottom) when we have enough comps.
    if trim_outliers and len(ppsfs) >= 5:
        ppsfs_trimmed = ppsfs[1:-1]
    else:
        ppsfs_trimmed = ppsfs

    median_ppsf = statistics.median(ppsfs_trimmed)

    def _quantile(data: list[float], q: float) -> float:
        if len(data) == 1:
            return data[0]
        pos = (len(data) - 1) * q
        lower = int(pos)
        frac = pos - lower
        if lower + 1 < len(data):
            return data[lower] + (data[lower + 1] - data[lower]) * frac
        return data[lower]

    p25 = _quantile(ppsfs_trimmed, 0.25)
    p75 = _quantile(ppsfs_trimmed, 0.75)

    arv = median_ppsf * subject_sqft if subject_sqft else median_ppsf
    arv_low = p25 * subject_sqft if subject_sqft else p25
    arv_high = p75 * subject_sqft if subject_sqft else p75

    # Confidence from sample size + dispersion (coefficient of variation).
    n = len(qualified)
    cv = (statistics.pstdev(ppsfs) / statistics.mean(ppsfs)) if len(ppsfs) >= 2 and statistics.mean(ppsfs) else 0
    if n >= 5 and cv < 0.15:
        confidence = "high"
    elif n >= 3:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "arv": round(arv, 0),
        "arv_low": round(arv_low, 0),
        "arv_high": round(arv_high, 0),
        "ppsf_median": round(median_ppsf, 2),
        "ppsf_spread": {"p25": round(p25, 2), "p75": round(p75, 2)},
        "coefficient_of_variation": round(cv, 3),
        "confidence": confidence,
        "qualified_count": n,
        "comps_used": sorted(qualified, key=lambda x: x.get("distance_mi") or 0)[:25],
        "comps_excluded": excluded[:25],
        "subject_sqft": subject_sqft,
        "note": "ARV = median $/sqft of qualified sold comps × subject sqft. Always sanity-check against a full walkthrough and a local agent's BPO.",
    }


@app.post("/api/comps")
def comps_endpoint(req: CompsRequest, db: Session = Depends(get_db)):
    """
    Compute ARV from comps. Uses manually-supplied comps when provided; otherwise
    pulls sold comps from ATTOM (when reachable). Optionally writes the ARV and
    repair estimate back onto the lead.
    """
    lead = db.get(Lead, req.lead_id) if req.lead_id else None

    subject_sqft = req.subject_sqft or (lead.sqft if lead else 0) or 0
    property_type = req.property_type or (lead.property_type if lead else "") or ""
    postal = req.postal_code or (lead.zip_code if lead else "")
    city = req.city or (lead.city if lead else "")
    state = req.state or (lead.state if lead else "TX")
    lat = req.latitude or (lead.latitude if lead else 0) or 0
    lon = req.longitude or (lead.longitude if lead else 0) or 0

    comps = [c.model_dump() for c in req.comps]
    source = "manual"

    if not comps:
        pulled = source_engine.fetch_sales_comps(
            postal_code=postal, city=city, state=state,
            latitude=lat, longitude=lon, radius_mi=req.radius_mi,
            months_back=req.months_back, property_type=property_type,
        )
        if pulled:
            comps = pulled
            source = "attom"

    if not comps:
        return {
            "source": "none",
            "arv": 0.0,
            "note": "No comps available. ATTOM is unreachable or returned nothing — "
                    "enter sold comps manually (address, sqft, sale price) to compute an ARV.",
            "qualified_count": 0,
        }

    result = compute_arv(subject_sqft, comps, sqft_tolerance_pct=req.sqft_tolerance_pct)
    result["source"] = source

    if req.save and lead:
        lead.arv = result["arv"]
        if req.repair_estimate:
            lead.repair_estimate = req.repair_estimate
        db.commit()
        result["saved_to_lead"] = lead.id

    return result


# ----- Follow-up tracking -------------------------------------------------- #

# "The fortune is in the follow-up." A sensible default touch cadence (days from
# today) indexed by how many times you've already reached out.
_FOLLOWUP_CADENCE = [1, 3, 7, 14, 30]


def default_followup_days(touch_count: int) -> int:
    if touch_count < len(_FOLLOWUP_CADENCE):
        return _FOLLOWUP_CADENCE[touch_count]
    return 30  # steady monthly cadence after the initial sequence


@app.post("/api/leads/{lead_id}/contact-log")
def add_contact_log(lead_id: str, payload: ContactLogInput, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")

    log = ContactLog(
        lead_id=lead_id,
        channel=payload.channel or "call",
        outcome=payload.outcome or "",
        notes=payload.notes or "",
    )
    db.add(log)

    lead.contact_count = (lead.contact_count or 0) + 1
    lead.last_contacted_at = _now()

    # Determine next follow-up date: explicit date > explicit days > cadence default.
    nxt = _parse_date(payload.next_follow_up_date)
    if not nxt and payload.next_follow_up_in_days is not None:
        nxt = _now() + timedelta(days=max(0, payload.next_follow_up_in_days))
    if not nxt:
        nxt = _now() + timedelta(days=default_followup_days(lead.contact_count))
    lead.next_follow_up_date = nxt

    # Auto-advance a brand-new lead to "Contacted" on first logged touch.
    if lead.status == "New":
        lead.status = "Contacted"

    db.commit()
    return {
        "logged": log.to_dict(),
        "lead": lead.to_dict(),
        "next_follow_up_date": nxt.isoformat(),
    }


@app.get("/api/leads/{lead_id}/contact-log")
def get_contact_log(lead_id: str, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    stmt = select(ContactLog).where(ContactLog.lead_id == lead_id).order_by(ContactLog.created_at.desc())
    logs = db.execute(stmt).scalars().all()
    return {"lead_id": lead_id, "count": len(logs), "logs": [l.to_dict() for l in logs]}


@app.get("/api/follow-ups/due")
def follow_ups_due(window: str = Query("today"), db: Session = Depends(get_db)):
    """
    window: 'overdue' (past due only), 'today' (due on/before today),
            'week' (due within 7 days), 'all' (any scheduled follow-up).
    """
    now = _now()
    stmt = select(Lead).where(Lead.next_follow_up_date.isnot(None))
    leads = db.execute(stmt).scalars().all()

    out = []
    for l in leads:
        due = l.next_follow_up_date
        if due is None:
            continue
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        delta_days = (due.date() - now.date()).days
        keep = (
            (window == "overdue" and delta_days < 0) or
            (window == "today" and delta_days <= 0) or
            (window == "week" and delta_days <= 7) or
            (window == "all")
        )
        if keep:
            d = l.to_dict()
            d["due_in_days"] = delta_days
            out.append(d)

    out.sort(key=lambda x: x["due_in_days"])
    return {"window": window, "count": len(out), "leads": out}


# ----- Buyer matching ------------------------------------------------------ #

def _estimated_offer_to_buyer(lead: Lead) -> float:
    """What you'd realistically list this deal to a cash buyer for."""
    if lead.asking_price:
        return lead.asking_price
    if lead.arv:
        # 70% rule, net of repairs, leaving your assignment fee inside the spread.
        return max(lead.arv * 0.70 - (lead.repair_estimate or 0), 0)
    if lead.est_value:
        return lead.est_value * 0.65
    return 0.0


def match_buyers_for_lead(lead: Lead, buyers: list[Buyer]) -> list[dict]:
    ask = _estimated_offer_to_buyer(lead)
    lead_city = (lead.city or "").strip().lower()
    lead_county = (lead.county or "").strip().lower()
    lead_zip = (lead.zip_code or "").strip()
    lead_ptype = (lead.property_type or "").strip().lower()
    now = _now()

    results = []
    for b in buyers:
        score = 0.0
        reasons: list[str] = []

        # Price fit (35)
        bmin, bmax = (b.budget_min or 0), (b.budget_max or 0)
        if ask and bmax > 0:
            if bmin <= ask <= bmax:
                score += 35; reasons.append(f"Price {fmt_money_plain(ask)} fits budget")
            elif bmax * 0.9 <= ask <= bmax * 1.1 or (bmin and bmin * 0.9 <= ask <= bmin * 1.1):
                score += 20; reasons.append("Price near budget edge")
        elif bmax == 0:
            score += 15; reasons.append("Budget not specified")

        # Geo fit (25)
        zips = b._csv(b.target_zips)
        cities = [c.lower() for c in b._csv(b.target_cities)]
        counties = [c.lower() for c in b._csv(b.target_counties)]
        if lead_zip and lead_zip in zips:
            score += 25; reasons.append(f"Targets ZIP {lead_zip}")
        elif (lead_city and lead_city in cities) or (lead_county and lead_county in counties):
            score += 18; reasons.append("In target city/county")
        elif not zips and not cities and not counties:
            score += 10; reasons.append("No geo filter set")

        # Asset type (10)
        assets = [a.lower() for a in b._csv(b.asset_types)]
        if not assets or (lead_ptype and lead_ptype in assets):
            score += 10
            if assets:
                reasons.append("Asset type match")

        # Beds (5)
        if not (b.min_beds or 0) or (lead.beds or 0) >= (b.min_beds or 0):
            score += 5

        # Rehab capacity (10)
        if not (b.max_rehab or 0):
            score += 7
        elif (lead.repair_estimate or 0) <= b.max_rehab:
            score += 10; reasons.append("Rehab within their budget")

        # Proof of funds (8)
        if b.pof_received:
            expired = b.pof_expires and (b.pof_expires.replace(tzinfo=timezone.utc) if b.pof_expires.tzinfo is None else b.pof_expires) < now
            if not expired:
                score += 8; reasons.append("Proof of funds on file")

        # Recent activity (up to 7)
        score += min(b.recent_cash_deals or 0, 7)
        if (b.recent_cash_deals or 0) >= 3:
            reasons.append(f"{b.recent_cash_deals} recent cash deals")

        d = b.to_dict()
        d["match_score"] = round(min(score, 100), 1)
        d["match_reasons"] = reasons
        results.append(d)

    results.sort(key=lambda x: x["match_score"], reverse=True)
    return results


@app.get("/api/leads/{lead_id}/matched-buyers")
def matched_buyers(lead_id: str, include_inactive: bool = False, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    stmt = select(Buyer)
    buyers = db.execute(stmt).scalars().all()
    if not include_inactive:
        buyers = [b for b in buyers if b.active]
    ranked = match_buyers_for_lead(lead, buyers)
    return {
        "lead_id": lead_id,
        "estimated_offer_to_buyer": round(_estimated_offer_to_buyer(lead), 0),
        "count": len(ranked),
        "buyers": ranked,
    }


# ----- Deal terms & deadlines ---------------------------------------------- #

@app.put("/api/leads/{lead_id}/deal-terms")
def update_deal_terms(lead_id: str, payload: DealTermsInput, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")

    data = payload.model_dump(exclude_unset=True)
    date_fields = {"contract_date", "inspection_end_date", "close_date"}
    for k, v in data.items():
        if k in date_fields:
            setattr(lead, k, _parse_date(v))
        elif k == "photo_urls":
            lead.photo_urls = json.dumps(v or [])
        else:
            setattr(lead, k, v)
    db.commit()
    return lead.to_dict()


@app.get("/api/deals/deadlines")
def deal_deadlines(db: Session = Depends(get_db)):
    """Every lead with an inspection or closing deadline, soonest first."""
    now = _now()
    stmt = select(Lead).where(
        (Lead.inspection_end_date.isnot(None)) | (Lead.close_date.isnot(None))
    )
    leads = db.execute(stmt).scalars().all()

    out = []
    for l in leads:
        events = []
        for label, dt in (("Inspection ends", l.inspection_end_date), ("Closing", l.close_date)):
            if dt:
                d = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
                events.append({"label": label, "date": d.date().isoformat(), "days_remaining": (d.date() - now.date()).days})
        if events:
            row = l.to_dict()
            row["deadlines"] = sorted(events, key=lambda e: e["days_remaining"])
            row["soonest_days"] = row["deadlines"][0]["days_remaining"]
            out.append(row)

    out.sort(key=lambda x: x["soonest_days"])
    return {"count": len(out), "deals": out}


# ----- Buyer update / proof of funds --------------------------------------- #

@app.put("/api/buyers/{buyer_id}")
def update_buyer(buyer_id: str, payload: BuyerUpdate, db: Session = Depends(get_db)):
    buyer = db.get(Buyer, buyer_id)
    if not buyer:
        raise HTTPException(404, "Buyer not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(buyer, k, v)
    if "recent_cash_deals" in data or "budget_max" in data or "entity_type" in data:
        buyer.cash_buyer_score = cash_buyer_score({
            "entity_type": buyer.entity_type or "",
            "recent_cash_deals": buyer.recent_cash_deals or 0,
            "budget_max": buyer.budget_max or 0,
        })
    db.commit()
    return buyer.to_dict()


@app.put("/api/buyers/{buyer_id}/pof")
def update_buyer_pof(buyer_id: str, payload: PofInput, db: Session = Depends(get_db)):
    buyer = db.get(Buyer, buyer_id)
    if not buyer:
        raise HTTPException(404, "Buyer not found")
    buyer.pof_received = payload.pof_received
    buyer.pof_amount = payload.pof_amount
    buyer.pof_date = _parse_date(payload.pof_date) or (_now() if payload.pof_received else None)
    buyer.pof_expires = _parse_date(payload.pof_expires)
    db.commit()
    return buyer.to_dict()


# ----- Property info sheet (PI sheet) -------------------------------------- #

def fmt_money_plain(v) -> str:
    try:
        return "${:,.0f}".format(float(v or 0))
    except (TypeError, ValueError):
        return "$0"


def build_pi_sheet_html(lead: Lead) -> str:
    arv = lead.arv or 0
    repair = lead.repair_estimate or 0
    ask = lead.asking_price or _estimated_offer_to_buyer(lead)
    spread = arv - ask - repair
    photos = lead.photos()
    sigs = lead.signals()

    photo_html = ""
    if photos:
        photo_html = '<div class="photos">' + "".join(
            f'<img src="{p}" alt="property photo">' for p in photos[:6]
        ) + "</div>"

    facts = [
        ("Beds", f"{lead.beds:g}" if lead.beds else "—"),
        ("Baths", f"{lead.baths:g}" if lead.baths else "—"),
        ("Sq Ft", f"{lead.sqft:,.0f}" if lead.sqft else "—"),
        ("Year built", f"{lead.year_built}" if lead.year_built else "—"),
        ("Lot", f"{lead.lot_sqft:,.0f} sqft" if lead.lot_sqft else "—"),
        ("Type", lead.property_type or "—"),
        ("Occupancy", lead.occupancy or "—"),
        ("County", lead.county or "—"),
    ]
    facts_html = "".join(f'<div class="fact"><span>{k}</span><b>{v}</b></div>' for k, v in facts)

    close_line = ""
    if lead.close_date:
        close_line = f"<b>Target close:</b> {lead.close_date.date().isoformat()} &nbsp;·&nbsp; "
    em_line = f"<b>Earnest money:</b> {fmt_money_plain(lead.earnest_money)}" if lead.earnest_money else ""

    sig_html = ""
    if sigs:
        sig_html = '<div class="sigs">' + "".join(f"<span>{s}</span>" for s in sigs) + "</div>"

    spread_color = "#15803d" if spread > 0 else "#b91c1c"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Property Info — {esc_html(lead.address)}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; color:#0f172a; margin:0; background:#f1f5f9; }}
  .sheet {{ max-width: 820px; margin: 24px auto; background:#fff; border-radius:14px; overflow:hidden; box-shadow:0 10px 40px rgba(2,6,23,.15); }}
  .hero {{ background: linear-gradient(135deg,#0b1220,#1e293b); color:#fff; padding:26px 30px; }}
  .hero .eyebrow {{ text-transform:uppercase; letter-spacing:.16em; font-size:11px; color:#67e8f9; font-weight:700; }}
  .hero h1 {{ margin:6px 0 2px; font-size:26px; }}
  .hero .loc {{ color:#cbd5e1; font-size:15px; }}
  .photos {{ display:grid; grid-template-columns:repeat(3,1fr); gap:3px; background:#0b1220; }}
  .photos img {{ width:100%; height:150px; object-fit:cover; display:block; }}
  .body {{ padding: 24px 30px 30px; }}
  .nums {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:0 0 22px; }}
  .num {{ background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; padding:14px; }}
  .num span {{ display:block; font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:#64748b; font-weight:700; }}
  .num b {{ font-size:21px; }}
  .facts {{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-bottom:22px; }}
  .fact {{ border-bottom:1px solid #eef2f7; padding:6px 0; }}
  .fact span {{ display:block; font-size:11px; color:#64748b; }}
  .fact b {{ font-size:15px; }}
  .sigs {{ margin:8px 0 20px; }}
  .sigs span {{ display:inline-block; background:#fef3c7; color:#92400e; border-radius:99px; padding:3px 10px; font-size:12px; font-weight:600; margin:0 6px 6px 0; }}
  h3 {{ margin:18px 0 8px; font-size:14px; text-transform:uppercase; letter-spacing:.06em; color:#334155; }}
  .terms {{ font-size:14px; color:#334155; line-height:1.7; }}
  .disc {{ margin-top:22px; padding:14px 16px; background:#f8fafc; border-left:3px solid #94a3b8; border-radius:6px; font-size:12px; color:#475569; line-height:1.6; }}
  .printbar {{ text-align:center; margin:16px; }}
  .printbar button {{ background:#0ea5e9; color:#fff; border:0; padding:11px 22px; border-radius:9px; font-size:14px; font-weight:700; cursor:pointer; }}
  @media print {{ body {{ background:#fff; }} .sheet {{ box-shadow:none; margin:0; }} .printbar {{ display:none; }} }}
</style></head>
<body>
<div class="printbar"><button onclick="window.print()">Print / Save as PDF</button></div>
<div class="sheet">
  <div class="hero">
    <div class="eyebrow">Investment Property · Off-Market</div>
    <h1>{esc_html(lead.address)}</h1>
    <div class="loc">{esc_html(lead.city)}, {esc_html(lead.state)} {esc_html(lead.zip_code)}</div>
  </div>
  {photo_html}
  <div class="body">
    <div class="nums">
      <div class="num"><span>ARV</span><b>{fmt_money_plain(arv)}</b></div>
      <div class="num"><span>Est. Repairs</span><b>{fmt_money_plain(repair)}</b></div>
      <div class="num"><span>Asking Price</span><b>{fmt_money_plain(ask)}</b></div>
      <div class="num"><span>Spread</span><b style="color:{spread_color}">{fmt_money_plain(spread)}</b></div>
    </div>
    <h3>Property details</h3>
    <div class="facts">{facts_html}</div>
    {('<h3>Why it&#39;s a deal</h3>' + sig_html) if sig_html else ''}
    <h3>Terms</h3>
    <div class="terms">{close_line}{em_line}<br>
      Cash or hard money, as-is, buyer covers closing. Assignable contract — assignment fee included in asking price.
    </div>
    <div class="disc">
      Figures are estimates provided in good faith and are not guaranteed. Buyer is responsible for independently
      verifying ARV, repair costs, square footage, condition, title, and all material facts before purchase.
      This is a wholesale assignment of an equitable interest in a purchase contract, not a listing by a licensed
      broker. Property sold strictly as-is, where-is.
    </div>
  </div>
</div>
</body></html>"""


def esc_html(s) -> str:
    return (str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


@app.get("/api/leads/{lead_id}/pi-sheet", response_class=HTMLResponse)
def pi_sheet(lead_id: str, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return HTMLResponse(build_pi_sheet_html(lead))


# ----- Buyer blast (generates messages; does not send) --------------------- #

@app.post("/api/leads/{lead_id}/blast")
def blast_deal(lead_id: str, limit: int = 10, db: Session = Depends(get_db)):
    """
    Build a ready-to-send deal blast for the best-matched buyers. Returns the
    matched buyer list plus email and SMS copy. It does NOT send anything —
    review and send from your own email/phone.
    """
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    buyers = [b for b in db.execute(select(Buyer)).scalars().all() if b.active]
    ranked = match_buyers_for_lead(lead, buyers)[:limit]

    ask = lead.asking_price or _estimated_offer_to_buyer(lead)
    arv = lead.arv or 0
    repair = lead.repair_estimate or 0
    loc = f"{lead.city}, {lead.state} {lead.zip_code}".strip()

    subject = f"Off-market deal: {lead.address} — {fmt_money_plain(ask)}"
    email_body = (
        f"Hi {{name}},\n\n"
        f"New off-market wholesale deal in {loc}:\n\n"
        f"Address: {lead.address}\n"
        f"ARV: {fmt_money_plain(arv)}\n"
        f"Estimated repairs: {fmt_money_plain(repair)}\n"
        f"Asking (assignment included): {fmt_money_plain(ask)}\n"
        + (f"Beds/Baths/SqFt: {lead.beds:g}/{lead.baths:g}/{lead.sqft:,.0f}\n" if lead.sqft else "")
        + (f"Target close: {lead.close_date.date().isoformat()}\n" if lead.close_date else "")
        + "\nCash or hard money, as-is, assignable contract. Reply or call if you want the full property info sheet "
        "and access details. First with proof of funds and earnest money locks it.\n\n"
        "Thanks!"
    )
    sms_body = (
        f"Off-market deal {lead.address}, {loc}. ARV {fmt_money_plain(arv)}, "
        f"repairs ~{fmt_money_plain(repair)}, asking {fmt_money_plain(ask)}. "
        "Cash/as-is, assignable. Want the PI sheet? Reply YES."
    )

    return {
        "lead_id": lead_id,
        "matched_buyers": ranked,
        "email": {"subject": subject, "body": email_body},
        "sms": {"body": sms_body},
        "note": "Personalize {name} per buyer. Review before sending — nothing is sent automatically.",
    }


# ── ATTOM / NETWORK DEBUG ENDPOINTS ──────────────────────────────────────── #
import logging
logging.basicConfig(level=logging.DEBUG)
_log = logging.getLogger("attom_debug")


@app.get("/api/debug/httpbin")
def debug_httpbin():
    """Check whether httpx can reach the open internet at all."""
    try:
        with httpx.Client(timeout=10) as c:
            r = c.get("https://httpbin.org/get")
            return {"ok": True, "status": r.status_code, "body": r.text[:500]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class AttomTestRequest(BaseModel):
    path: str = "/property/basicprofile"
    params: dict = {}


@app.post("/api/debug/attom-test")
def debug_attom_test(req: AttomTestRequest):
    """
    Make a real ATTOM request and return full diagnostics.
    Example body: {"path": "/property/basicprofile", "params": {"address1": "1 Main St", "address2": "Dallas, TX 75201"}}
    """
    base = os.getenv("ATTOM_BASE", "https://api.developer.attomdata.com").rstrip("/")
    key  = os.getenv("ATTOM_API_KEY", "").strip()
    url  = f"{base}{req.path}"
    masked_key = f"{'*' * 8}{key[-4:]}" if len(key) > 4 else ("NOT SET" if not key else "TOO SHORT")

    _log.debug("ATTOM DEBUG REQUEST → %s  params=%s  key=%s", url, req.params, masked_key)

    result: dict = {
        "url": url,
        "masked_key": masked_key,
        "params": req.params,
        "attom_key_set": bool(key),
        "attom_base_env": os.getenv("ATTOM_BASE", "(using default)"),
    }

    # Step 1 — raw connectivity to ATTOM root
    try:
        with httpx.Client(timeout=10) as c:
            ping = c.get("https://api.developer.attomdata.com")
            result["connectivity"] = {"status": ping.status_code, "ok": True}
    except Exception as e:
        result["connectivity"] = {"ok": False, "error": str(e)}

    # Step 2 — actual API call
    if not key:
        result["attom_call"] = {"skipped": "ATTOM_API_KEY not set"}
        return result

    headers = {
        "apikey": key,
        "Accept": "application/json",
    }
    _log.debug("ATTOM headers (masked): apikey=%s Accept=application/json", masked_key)

    try:
        with httpx.Client(timeout=15) as c:
            r = c.get(url, headers=headers, params=req.params)
            _log.debug("ATTOM response: %s", r.status_code)
            result["attom_call"] = {
                "status_code": r.status_code,
                "response_headers": dict(r.headers),
                "body_preview": r.text[:500],
            }
    except httpx.TimeoutException as e:
        result["attom_call"] = {"error": "TIMEOUT", "detail": str(e)}
    except httpx.ConnectError as e:
        result["attom_call"] = {"error": "CONNECTION_FAILED", "detail": str(e)}
    except Exception as e:
        result["attom_call"] = {"error": type(e).__name__, "detail": str(e)}

    return result


# Serve the dashboard assets/pages last so /api routes win.
app.mount("/dashboard", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
