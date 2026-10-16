"""Create demo users + categories (run AFTER `alembic upgrade head`):  python seed.py"""
import asyncio

from sqlalchemy import select

from app.auth.security import hash_password
from app.database import SessionLocal
from app.models.models import Agent, Availability, Category, Role, User

USERS = [
    ("Admin User", "admin@example.com", "Admin@123", Role.admin),
    ("Alice Agent", "agent1@example.com", "Agent@123", Role.agent),
    ("Bob Agent", "agent2@example.com", "Agent@123", Role.agent),
    ("Carol Customer", "customer@example.com", "Customer@123", Role.customer),
]
CATEGORIES = ["Billing", "Technical", "Account", "Delivery"]


async def main():
    async with SessionLocal() as db:
        cats = []
        for name in CATEGORIES:
            c = (await db.execute(select(Category).where(Category.category_name == name))).scalar_one_or_none()
            if not c:
                c = Category(category_name=name, description=f"{name} related issues")
                db.add(c)
            cats.append(c)
        await db.flush()
        for name, email, pwd, role in USERS:
            if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
                continue
            user = User(name=name, email=email, hashed_password=hash_password(pwd), role=role)
            db.add(user)
            if role == Role.agent:
                db.add(Agent(user=user, categories=cats, availability=Availability.offline))
        await db.commit()
    print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(main())
