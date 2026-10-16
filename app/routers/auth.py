from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_roles
from app.auth.security import create_access_token, hash_password, verify_password
from app.config import settings
from app.database import get_db
from app.models.models import Role, User
from app.schemas.schemas import RegisterIn, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["Auth"])


async def _create_user(db: AsyncSession, body: RegisterIn, role: Role) -> User:
    exists = (await db.execute(select(User.id).where(User.email == body.email.lower()))).first()
    if exists:
        raise HTTPException(409, "Email already registered")
    user = User(name=body.name, email=body.email.lower(), hashed_password=hash_password(body.password), role=role)
    db.add(user)
    await db.commit()
    return user


@router.post("/register", response_model=UserOut, status_code=201)
async def register(body: RegisterIn, db: AsyncSession = Depends(get_db)):
    """Public registration - always creates a Customer."""
    return await _create_user(db, body, Role.customer)


@router.post("/admins", response_model=UserOut, status_code=201)
async def create_admin(body: RegisterIn, _: User = Depends(require_roles(Role.admin)),
                       db: AsyncSession = Depends(get_db)):
    """Admin only - create another Admin account."""
    return await _create_user(db, body, Role.admin)


@router.post("/login", response_model=TokenOut)
async def login(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    """`username` = email."""
    user = (await db.execute(select(User).where(User.email == form.username.lower()))).scalar_one_or_none()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(401, "Incorrect email or password", headers={"WWW-Authenticate": "Bearer"})
    if not user.is_active:
        raise HTTPException(403, "Account is inactive")
    return TokenOut(access_token=create_access_token(user.id, user.role.value),
                    expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user
