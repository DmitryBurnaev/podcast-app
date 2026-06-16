import logging
import sys
from typing import TYPE_CHECKING

from http import HTTPStatus

from src.modules.schemas.errors import ErrorCode
from src.constants import ResponseCode

if TYPE_CHECKING:
    from src.modules.tasks.base import TaskResultCode

sys.modules.setdefault("exceptions", sys.modules[__name__])
sys.modules.setdefault("src.exceptions", sys.modules[__name__])


class BaseApplicationError(Exception):
    """Base application error"""

    message = "Something went wrong"
    details: str | dict | None = None
    log_level: int = logging.ERROR
    log_message: str = "Application error"
    status_code: int = HTTPStatus.INTERNAL_SERVER_ERROR
    response_code: ResponseCode = ResponseCode.INTERNAL_ERROR

    # default_status_code: int = HTTPStatus.INTERNAL_SERVER_ERROR
    # default_response_code: ResponseCode = ResponseCode.INTERNAL_ERROR

    def __init__(
        self,
        details: str | dict | None = None,
        message: str | None = None,
        status_code: int | None = None,
        response_code: ResponseCode | None = None,
    ):
        self.message = message or self.message
        self.details = details or self.details
        self.status_code = status_code or self.status_code
        self.response_code = response_code or self.response_code

        # if status_code is not None:
        #     self.status_code = status_code
        # if response_code is not None:
        #     self.response_code = response_code

        # self.status_code: int = status_code or getattr(
        #     self, "status_code", self.default_status_code
        # )
        # self.response_code = response_code or getattr(
        #     self,
        #     "response_code",
        #     self.default_response_code,
        # )

    def __str__(self) -> str:
        return f"{self.message} ({self.details})"

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} {self.message} ({self.details})>"


class AppSettingsError(BaseApplicationError):
    message = "Unable to instantiate application settings"


class StartupError(BaseApplicationError):
    message = "Unable to start application"


class DatabaseError(BaseApplicationError):
    message = "Something wrong with the database communication"


class NotSupportedError(BaseApplicationError):
    message = "Requested operation is not supported"


class ImproperlyConfiguredError(BaseApplicationError):
    message = "Required settings are not provided for requested action"


class UnexpectedError(BaseApplicationError):
    message = "Something unexpected happened."


class S3UploadingError(BaseApplicationError):
    message = "Couldn't upload file to the storage"


class HttpError(BaseApplicationError):
    message = "Some HTTP error happened."


class AuthenticationFailedError(BaseApplicationError):
    status_code = 401
    response_code = ResponseCode.AUTH_FAILED
    message = "Authentication credentials are invalid."


# TODO: rework/optimize auth
class AuthenticationRequiredError(BaseApplicationError):
    status_code = 401
    response_code = ResponseCode.MISSED_CREDENTIALS
    message = "Authentication is required."


class SignatureExpiredError(BaseApplicationError):
    status_code = 401
    response_code = ResponseCode.SIGNATURE_EXPIRED
    message = "Authentication credentials are invalid."


class PermissionDeniedError(BaseApplicationError):
    status_code = 403
    message = "You don't have permission to perform this action."
    response_code = ResponseCode.FORBIDDEN


class NotFoundError(BaseApplicationError):
    status_code = 404
    message = "Requested object not found."
    response_code = ResponseCode.NOT_FOUND


class MethodNotAllowedError(BaseApplicationError):
    status_code = 405
    message = "Requested method is not allowed."
    response_code = ResponseCode.NOT_ALLOWED


class InviteTokenInvalidationError(BaseApplicationError):
    status_code = 401
    message = "Requested token is expired or does not exist."
    response_code = ResponseCode.INVITE_ERROR


class InvalidRequestError(BaseApplicationError):
    status_code = 400
    message = "Requested data is not valid."
    response_code = ResponseCode.INVALID_PARAMETERS


class InvalidResponseError(BaseApplicationError):
    status_code = 500
    message = "Response data couldn't be serialized."


class SendRequestError(BaseApplicationError):
    status_code = 503
    message = "Got unexpected error for sending request."
    request_url = ""
    response_code = ResponseCode.COMMUNICATION_ERROR

    def __init__(self, message: str, details: str, request_url: str):
        super().__init__(details, message)
        self.request_url = request_url


class MaxAttemptsReached(BaseApplicationError):
    status_code = 503
    message = "Reached max attempt to make action"


class EmailSendingError(BaseApplicationError):
    status_code = 503
    message = "Couldn't send email to recipient"
    response_code = ResponseCode.COMMUNICATION_ERROR


class UserCancellationError(BaseApplicationError):
    message = "Current processing was interrupted"


class StorageConfigurationError(BaseApplicationError):
    message = "Missing storage configuration"
    status_code = 500


class FFMPegPreparationError(BaseApplicationError):
    message = "We couldn't prepare file by ffmpeg"


class FFMPegParseError(BaseApplicationError):
    message = "We couldn't parse info from ffmpeg"


class SourceFetchError(BaseApplicationError):
    message = "We couldn't extract info about requested episode."


class DownloadingInterrupted(Exception):
    def __init__(self, code: "TaskResultCode", message: str = ""):
        self.code = code
        self.message = message

    def __repr__(self):
        return f'DownloadingInterrupted({self.code.name}, "{self.message}")'


# ======
# API Errors
# ======
class APIError(BaseApplicationError):
    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    message = "Something went wrong."


class AuthMissingAPIError(APIError):
    code = ErrorCode.AUTH_MISSING
    message = "Authentication credentials were not provided."
    status_code = 401


class AuthInvalidAPIError(APIError):
    code = ErrorCode.AUTH_INVALID
    message = "Authentication credentials are invalid."
    status_code = 401


class TokenExpiredAPIError(APIError):
    code = ErrorCode.TOKEN_EXPIRED
    message = "Access token expired."
    status_code = 401


class RefreshExpiredAPIError(APIError):
    code = ErrorCode.REFRESH_EXPIRED
    message = "Refresh token expired."
    status_code = 401


class SessionInactiveAPIError(APIError):
    code = ErrorCode.SESSION_INACTIVE
    message = "Session is inactive or expired."
    status_code = 401


class ForbiddenAPIError(APIError):
    code = ErrorCode.FORBIDDEN
    message = "You do not have permission to perform this action."
    status_code = 403


class InvalidParametersAPIError(APIError):
    code = ErrorCode.INVALID_PARAMETERS
    message = "Requested data is not valid."
    status_code = 400


class NotFoundAPIAPIError(APIError):
    code = ErrorCode.NOT_FOUND
    message = "Requested object was not found."
    status_code = 404


class StateConflictAPIError(APIError):
    code = ErrorCode.CONFLICT
    message = "Requested operation conflicts with the current state."
    status_code = 409
