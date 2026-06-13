"""Interceptor agent entry point — Team 3 stub."""
import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [agent] %(message)s")
log = logging.getLogger(__name__)


def main() -> None:
    log.info("interceptor_agent starting (stub — Team 3 implementation pending)")
    while True:
        time.sleep(5)
        log.info("interceptor_agent running…")


if __name__ == "__main__":
    main()
