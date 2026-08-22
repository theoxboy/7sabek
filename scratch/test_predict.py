import asyncio
import os
import sys
from sqlalchemy import select

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.db.session import get_sessionmaker
from app.models import User
from app.api.routes.nlp import predict_nlp, NLPPredictRequest

async def main():
    sm = get_sessionmaker()
    async with sm() as session:
        user_stmt = select(User).where(User.email == "futurehallal@gmail.com")
        user = (await session.execute(user_stmt)).scalars().first()
        if not user:
            print("User not found")
            return
        
        req = NLPPredictRequest(text="activite 50 dhs lbareh")
        try:
            res = await predict_nlp(req, session, user)
            print("Prediction output:", res)
        except Exception as e:
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
