import asyncio
import os
import sys
from sqlalchemy import select

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.db.session import get_sessionmaker
from app.models import User, Category

async def main():
    sm = get_sessionmaker()
    async with sm() as session:
        user_stmt = select(User).where(User.email == "futurehallal@gmail.com")
        user = (await session.execute(user_stmt)).scalars().first()
        if not user:
            print("User not found")
            return
        
        cat_stmt = select(Category).where(Category.user_id == user.id)
        categories = (await session.execute(cat_stmt)).scalars().all()
        
        print(f"Categories for {user.email}:")
        for cat in categories:
            print(f"ID: {cat.id} | Name: {cat.name} | Kind: {getattr(cat, 'kind', 'N/A')}")

if __name__ == "__main__":
    asyncio.run(main())
