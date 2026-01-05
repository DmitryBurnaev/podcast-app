from fastapi import Depends

from src.modules.auth.tokens import verify_api_token

__all__ = ("verify_api_token",)

VerifyAPITokenDep = Depends(verify_api_token)
