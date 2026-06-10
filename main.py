from dotenv import load_dotenv
from fastapi import FastAPI

from config import settings
from logger import logger


load_dotenv()

app = FastAPI()


@app.get("/")
async def root():
    logger.info(settings.stripe_api_key)
    logger.info(settings.stripe_webhook_secret)
    logger.info(settings.kintsugi_api_key)
    return {"message": "Hello World"}
