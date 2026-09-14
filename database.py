"""
database.py
------------
Handles the MongoDB connection and provides async CRUD helper functions
for the `users` and `deals` collections using Motor (the async PyMongo
driver). Designed so the rest of the bot never talks to MongoDB directly
-- everything goes through this module.
"""

import os
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import PyMongoError
from pymongo import ReturnDocument

logger = logging.getLogger(__name__)

MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://Ritik:Ritikraj@ritikraj.cciyuco.mongodb.net/?retryWrites=true&w=majority")
DB_NAME = os.getenv("DB_NAME", "escrow_bot")

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


class DealStatus:
    """Central place for every valid deal status string used across the app."""
    AWAITING_SELLER = "AWAITING_SELLER"   # buyer created it, no seller yet
    AWAITING_PAYMENT = "AWAITING_PAYMENT"  # seller joined, waiting on buyer to pay
    FUNDS_HELD = "FUNDS_HELD"             # money is in escrow
    DISPUTED = "DISPUTED"                 # one side raised a dispute
    RELEASED = "RELEASED"                 # funds released to seller (deal complete)
    CANCELLED = "CANCELLED"               # cancelled before any funds moved
    REFUNDED = "REFUNDED"                 # admin resolved a dispute in buyer's favor


async def init_db() -> AsyncIOMotorDatabase:
    """
    Establishes the MongoDB connection (idempotent -- safe to call more than
    once) and ensures required indexes exist. Call this once at startup
    before any handler touches the database.
    """
    global _client, _db
    if _db is not None:
        return _db
    try:
        _client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        # Force a round-trip so connection issues surface immediately at startup
        # instead of on the first user interaction.
        await _client.admin.command("ping")
        _db = _client[DB_NAME]
        await _db.users.create_index("user_id", unique=True)
        await _db.deals.create_index("deal_id", unique=True)
        await _db.deals.create_index("buyer_id")
        await _db.deals.create_index("seller_id")
        logger.info("Connected to MongoDB at %s (db=%s)", MONGO_URI, DB_NAME)
        return _db
    except PyMongoError:
        logger.exception("Failed to connect to MongoDB")
        raise


def get_db() -> AsyncIOMotorDatabase:
    """Returns the initialized database handle. Raises if init_db() wasn't awaited yet."""
    if _db is None:
        raise RuntimeError("Database not initialized. Call init_db() before using database.py functions.")
    return _db


# ---------------------------------------------------------------------------
# User operations
# ---------------------------------------------------------------------------

async def upsert_user(user_id: int, username: Optional[str], full_name: str) -> Dict[str, Any]:
    """Creates the user on first contact, or refreshes their cached name/username."""
    db = get_db()
    now = datetime.now(timezone.utc)
    doc = await db.users.find_one_and_update(
        {"user_id": user_id},
        {
            "$set": {"username": username, "full_name": full_name, "updated_at": now},
            "$setOnInsert": {"created_at": now, "deals_count": 0, "is_banned": False},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc


async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    db = get_db()
    return await db.users.find_one({"user_id": user_id})


async def increment_user_deal_count(user_id: int) -> None:
    db = get_db()
    await db.users.update_one({"user_id": user_id}, {"$inc": {"deals_count": 1}})


# ---------------------------------------------------------------------------
# Deal operations
# ---------------------------------------------------------------------------

def generate_deal_id() -> str:
    """Generates a short, human-shareable, unique-enough deal ID (e.g. 'A1B2C3D4')."""
    return uuid.uuid4().hex[:8].upper()


async def create_deal(
    buyer_id: int,
    amount: float,
    currency: str,
    description: str = "",
) -> Dict[str, Any]:
    """Inserts a brand-new deal in AWAITING_SELLER status and returns the document."""
    db = get_db()
    now = datetime.now(timezone.utc)

    deal_id = generate_deal_id()
    # Practically impossible, but guard against a collision anyway.
    while await db.deals.find_one({"deal_id": deal_id}):
        deal_id = generate_deal_id()

    deal = {
        "deal_id": deal_id,
        "buyer_id": buyer_id,
        "seller_id": None,
        "amount": round(float(amount), 8),
        "currency": currency.upper(),
        "description": description,
        "status": DealStatus.AWAITING_SELLER,
        "dispute_reason": None,
        "created_at": now,
        "updated_at": now,
    }
    await db.deals.insert_one(deal)
    await increment_user_deal_count(buyer_id)
    return deal


async def get_deal(deal_id: str) -> Optional[Dict[str, Any]]:
    db = get_db()
    return await db.deals.find_one({"deal_id": deal_id.upper()})


async def get_user_deals(user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    """Returns the user's most recent deals, whether they're the buyer or the seller."""
    db = get_db()
    cursor = (
        db.deals.find({"$or": [{"buyer_id": user_id}, {"seller_id": user_id}]})
        .sort("created_at", -1)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def update_deal_status(
    deal_id: str,
    new_status: str,
    extra_fields: Optional[Dict[str, Any]] = None,
    expected_status: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Atomically transitions a deal to `new_status`.

    If `expected_status` is given, the write only applies when the deal's
    CURRENT status matches it. This is what prevents race conditions --
    e.g. two people tapping "Release Funds" at the same instant, or a
    dispute being raised on a deal that was simultaneously released.
    Returns None if the expected-status guard failed (caller should treat
    that as "state changed under you, please refresh").
    """
    db = get_db()
    query: Dict[str, Any] = {"deal_id": deal_id.upper()}
    if expected_status:
        query["status"] = expected_status

    fields = {"status": new_status, "updated_at": datetime.now(timezone.utc)}
    if extra_fields:
        fields.update(extra_fields)

    return await db.deals.find_one_and_update(
        query, {"$set": fields}, return_document=ReturnDocument.AFTER
    )


async def assign_seller(deal_id: str, seller_id: int) -> Optional[Dict[str, Any]]:
    """
    Atomically claims an open deal for a seller. The filter requires
    seller_id to still be None and status to still be AWAITING_SELLER, so
    only the first person to tap "Join as Seller" succeeds.
    """
    db = get_db()
    return await db.deals.find_one_and_update(
        {"deal_id": deal_id.upper(), "status": DealStatus.AWAITING_SELLER, "seller_id": None},
        {
            "$set": {
                "seller_id": seller_id,
                "status": DealStatus.AWAITING_PAYMENT,
                "updated_at": datetime.now(timezone.utc),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
