import argparse
import logging
import os
from xml.sax.saxutils import quoteattr

from dotenv import load_dotenv
from twilio.rest import Client

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("twilio.http_client").setLevel(logging.WARNING)
log = logging.getLogger(__name__)


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing env var: {name}")
    return value


def to_wss(url: str) -> str:
    return url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Place an outbound DialAgent call.")
    p.add_argument("task", help="Plain-English task for the agent to pursue on the call.")
    p.add_argument("--to", help="Destination phone number (E.164). Defaults to $JAY_CELL.")
    return p.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    account_sid = require_env("TWILIO_ACCOUNT_SID")
    auth_token = require_env("TWILIO_AUTH_TOKEN")
    from_number = require_env("TWILIO_PHONE_NUMBER")
    to_number = args.to or require_env("JAY_CELL")
    ngrok_url = require_env("NGROK_URL")

    ws_url = f"{to_wss(ngrok_url)}/ws"
    twiml = (
        f"<Response><Connect>"
        f'<Stream url="{ws_url}">'
        f"<Parameter name=\"task\" value={quoteattr(args.task)} />"
        f"</Stream></Connect></Response>"
    )

    client = Client(account_sid, auth_token)
    call = client.calls.create(to=to_number, from_=from_number, twiml=twiml)
    log.info("placed call: sid=%s to=%s task=%r", call.sid, to_number, args.task)


if __name__ == "__main__":
    main()
