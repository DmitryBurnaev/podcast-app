from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, SecretStr, model_validator


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


class PasswordConfirmationMixin(BaseModel):
    """Validate the repeated password used by account-management requests."""

    password_1: str = Field(min_length=1, max_length=128)
    password_2: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_passwords_match(self) -> "PasswordConfirmationMixin":
        if self.password_1 != self.password_2:
            raise ValueError("Passwords do not match.")
        return self


class SignUpRequest(PasswordConfirmationMixin):
    """Create an account from an unexpired invitation."""

    email: EmailStr
    invite_token: str = Field(min_length=1, max_length=32)


class InviteUserRequest(BaseModel):
    """Invite a user by email."""

    email: EmailStr


class UserInviteResponse(BaseModel):
    """Invitation metadata exposed via API."""

    id: int
    email: EmailStr
    is_applied: bool
    expired_at: datetime
    created_at: datetime


class ResetPasswordRequest(BaseModel):
    """Request a password-reset email."""

    email: EmailStr


class ChangePasswordRequest(PasswordConfirmationMixin):
    """Apply a password-reset token."""

    token: str = Field(min_length=1, max_length=1024)


class ProfileUpdateRequest(BaseModel):
    """Editable profile fields."""

    email: EmailStr | None = None
    password_1: str | None = Field(default=None, min_length=1, max_length=128)
    password_2: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_passwords_match(self) -> "ProfileUpdateRequest":
        if self.password_1 != self.password_2:
            raise ValueError("Passwords do not match.")
        return self


class UserIPResponse(BaseModel):
    """Hashed address registered for the current user."""

    id: int
    hashed_address: str
    registered_by: str
    created_at: datetime


class DeleteUserIPsRequest(BaseModel):
    """IDs of IP history entries to delete."""

    ids: list[int] = Field(min_length=1)


class UserAccessTokenCreateRequest(BaseModel):
    """Create a named long-lived API token."""

    name: str = Field(min_length=1, max_length=256)
    expires_in_days: int = Field(gt=0)


class UserAccessTokenUpdateRequest(BaseModel):
    """Editable fields of an API token."""

    name: str | None = Field(default=None, min_length=1, max_length=256)
    enabled: bool | None = None


class UserAccessTokenResponse(BaseModel):
    """API token metadata. The hashed stored token is intentionally omitted."""

    id: int
    name: str
    enabled: bool
    expires_in: datetime
    created_at: datetime


class CreatedUserAccessTokenResponse(UserAccessTokenResponse):
    """API token metadata including the raw token shown only once."""

    token: str
