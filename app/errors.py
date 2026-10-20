from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from app.generated.models import ApiError


class BusinessError(Exception):
    def __init__(self, code, status, message, details=None):
        self.code = code
        self.status = status
        self.message = message
        self.details = details


def error_response(code, status, message, details=None):
    error = ApiError(error_code=code, message=message, details=details)
    return JSONResponse(status_code=status, content=error.model_dump(mode='json', exclude_none=True), headers={'WWW-Authenticate':'Bearer'} if status == 401 else None)


async def business_error(request: Request, exception: BusinessError):
    return error_response(exception.code, exception.status, exception.message, exception.details)


async def validation_error(request: Request, exception: RequestValidationError):
    fields = [{'field': '.'.join(str(part) for part in item['loc']), 'message': item['msg']} for item in exception.errors()]
    return error_response('VALIDATION_ERROR', 400, 'Некорректные данные запроса', {'fields':fields})


async def http_error(request: Request, exception: HTTPException):
    codes = {404:'NOT_FOUND',405:'METHOD_NOT_ALLOWED',401:'TOKEN_INVALID',403:'ACCESS_DENIED'}
    return error_response(codes.get(exception.status_code,'VALIDATION_ERROR'), exception.status_code, str(exception.detail))
