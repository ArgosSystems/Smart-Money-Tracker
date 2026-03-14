import asyncio
import sys
import argparse
from sqlalchemy import select
from api.models import init_db, AsyncSessionLocal, SmartLabel

async def add_pro_label(address: str, chain: str, name: str, entity_type: str):
    await init_db()
    
    address = address.lower()
    
    async with AsyncSessionLocal() as db:
        existing = await db.scalar(
            select(SmartLabel).where(
                SmartLabel.address == address,
                SmartLabel.chain == chain
            )
        )
        
        if existing:
            print(f"[{chain}] {address} already exists as '{existing.name}' (Tier: {existing.tier}).")
            return
            
        new_lbl = SmartLabel(
            address=address,
            chain=chain,
            name=name,
            entity_type=entity_type,
            tier="pro"
        )
        db.add(new_lbl)
        await db.commit()
        
    print(f"Successfully added PRO label: [{chain}] {address} -> {name} ({entity_type})")

def main():
    parser = argparse.ArgumentParser(description="Add a PRO smart label manually.")
    parser.add_argument("address", help="The wallet address")
    parser.add_argument("chain", help="The network chain (e.g., ethereum, solana)")
    parser.add_argument("name", help="The display name (e.g., 'a16z')")
    parser.add_argument("entity_type", help="The type of entity (e.g., vc, exchange)")
    
    args = parser.parse_args()
    
    asyncio.run(add_pro_label(args.address, args.chain, args.name, args.entity_type))

if __name__ == "__main__":
    main()
