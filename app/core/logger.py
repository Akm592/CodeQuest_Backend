# Logging setup
import logging
from app.core.config import settings


def configure_logger():
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s - {%(pathname)s:%(lineno)d}",
        handlers=[logging.StreamHandler(), logging.FileHandler(settings.APP_LOG_FILE)],
    )
    return logging.getLogger(__name__)


logger = configure_logger()
