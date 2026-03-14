from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from api.models import GuildSubscription, SmartLabel, get_db

router = APIRouter(prefix="/api/v1/guilds", tags=["Guilds"])

class GuildCreate(BaseModel):
    guild_id: str

class GuildResponse(BaseModel):
    guild_id: str
    tier: str

class WhoIsResponse(BaseModel):
    address: str
    name: Optional[str] = None
    entity_type: Optional[str] = None
    tier: Optional[str] = None
    is_masked: bool = False

@router.post("", response_model=GuildResponse, status_code=201)
async def create_guild(body: GuildCreate, db: AsyncSession = Depends(get_db)):
    guild = await db.scalar(select(GuildSubscription).where(GuildSubscription.guild_id == body.guild_id))
    if not guild:
        guild = GuildSubscription(guild_id=body.guild_id, tier="free")
        db.add(guild)
        await db.commit()
    return {"guild_id": guild.guild_id, "tier": guild.tier}

@router.get("/{guild_id}/tier", response_model=GuildResponse)
async def get_guild_tier(guild_id: str, db: AsyncSession = Depends(get_db)):
    guild = await db.scalar(select(GuildSubscription).where(GuildSubscription.guild_id == guild_id))
    if not guild:
         # Lazily auto-create on tier check if missing
        guild = GuildSubscription(guild_id=guild_id, tier="free")
        db.add(guild)
        await db.commit()
    return {"guild_id": guild.guild_id, "tier": guild.tier}

@router.patch("/{guild_id}/upgrade", response_model=GuildResponse)
async def upgrade_guild(guild_id: str, db: AsyncSession = Depends(get_db)):
    guild = await db.scalar(select(GuildSubscription).where(GuildSubscription.guild_id == guild_id))
    if not guild:
        guild = GuildSubscription(guild_id=guild_id, tier="pro")
    else:
        guild.tier = "pro"
    db.add(guild)
    await db.commit()
    return {"guild_id": guild.guild_id, "tier": guild.tier}

@router.get("/{guild_id}/whois/{address}", response_model=WhoIsResponse)
async def whois_lookup(guild_id: str, address: str, chain: str = "ethereum", db: AsyncSession = Depends(get_db)):
    address = address.lower()
    
    # Get guild tier
    guild = await db.scalar(select(GuildSubscription).where(GuildSubscription.guild_id == guild_id))
    tier = guild.tier if guild else "free"
    
    # Look up smart label
    label = await db.scalar(
        select(SmartLabel).where(SmartLabel.address == address, SmartLabel.chain == chain)
    )
    
    if not label:
        return {"address": address, "name": None, "entity_type": None, "tier": None, "is_masked": False}
        
    if tier == "free" and label.tier == "pro":
        return {
            "address": address,
            "name": "🤖 Smart Entity (Pro 🔒)",
            "entity_type": "hidden",
            "tier": "pro",
            "is_masked": True
        }
        
    return {
        "address": address,
        "name": label.name,
        "entity_type": label.entity_type,
        "tier": label.tier,
        "is_masked": False
    }
