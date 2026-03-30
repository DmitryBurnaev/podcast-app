import uuid
from typing import Any, Literal

from litestar import get, post, Request
from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException
from litestar.response import Template

from src.modules.views.base import BaseController
from src.schemas import User, UserLoginPayload, UserCreatePayload


class ProfileController(BaseController):

    @get("/profile")
    async def get(self) -> Template:
        return self.get_response_template(
            template_name="profile.html",
            context={
                "title": "Profile",
                "current": "profile",
                "current_user": "Test User",
            },
        )


async def retrieve_user_handler(session: dict[str, Any], connection: ASGIConnection) -> User | None:
    # we retrieve the user instance based on session data
    value = await connection.cache.get(session.get("user_id", ""))
    if value:
        return User(**value)

    return None


@post("/login")
async def login(data: UserLoginPayload, request: Request) -> User:
    # we received log-in data via post.
    # our login handler should retrieve from persistence (a db etc.)
    # the user data and verify that the login details
    # are correct. If we are using passwords, we should check that
    # the password hashes match etc. We will simply assume that we
    # have done all of that we now have a user value:
    user_id = await request.cache.get(data.email)

    if not user_id:
        raise NotAuthorizedException

    user_data = await request.cache.get(user_id)

    # once verified we can create a session.
    # to do this we simply need to call the Starlite
    # 'Request.set_session' method, which accepts either dictionaries
    # or pydantic models. In our case, we can simply record a
    # simple dictionary with the user ID value:
    request.set_session({"user_id": user_id})

    # you can do whatever we want here. In this case, we will simply return the user data:
    return User(**user_data)


# the endpoint below requires the user to be already authenticated
# to be able to access it.
@get("/user")
def get_user(request: Request[User, dict[Literal["user_id"], str]]) -> Any:
    # because this route requires authentication, we can access
    # `request.user`, which is the authenticated user returned
    # by the 'retrieve_user_handler' function we passed to SessionAuth.
    return request.user
