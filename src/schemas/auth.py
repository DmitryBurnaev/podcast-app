from pydantic import BaseModel, EmailStr, Field, SecretStr


class User(BaseModel):
    """DTO aligned with ORM `auth_users`."""

    id: int
    email: EmailStr


class UserCreatePayload(BaseModel):
    """Payload for creating a user."""

    name: str
    email: EmailStr
    password: SecretStr


class UserLoginPayload(BaseModel):
    """Payload for logging in a user."""

    email: EmailStr
    password: SecretStr


class SignInRequest(BaseModel):
    """API request payload for signing in."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshTokenRequest(BaseModel):
    """API request payload for refreshing an auth token pair."""

    refresh_token: str = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    """API response with issued auth tokens."""

    access_token: str
    refresh_token: str


class UserResponse(BaseModel):
    """API response with authenticated user details."""

    id: int
    email: EmailStr
    is_active: bool
    is_superuser: bool
