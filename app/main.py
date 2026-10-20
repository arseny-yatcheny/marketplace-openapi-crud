import logging
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from app.errors import BusinessError, business_error, validation_error, http_error
from app.generated.routes import router
from app.middleware import RequestLoggingMiddleware
from app.validation import CONTRACT

logging.basicConfig(level=logging.INFO,format='%(message)s')
app = FastAPI(title='Marketplace API',version='1.0.0',redoc_url=None)
app.openapi = lambda: CONTRACT
app.add_exception_handler(BusinessError,business_error)
app.add_exception_handler(RequestValidationError,validation_error)
app.add_exception_handler(HTTPException,http_error)
app.include_router(router)
app.add_middleware(RequestLoggingMiddleware)
