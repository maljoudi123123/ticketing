"""Submit the appendix tickets through the public ingestion endpoint."""

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    tickets = json.loads(Path(__file__).with_name("tickets.json").read_text(encoding="utf-8"))
    for ticket in tickets:
        request = Request(
            args.base_url.rstrip("/") + "/tickets",
            data=json.dumps(ticket).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=10) as response:
                print(f"{ticket['id']} {response.status} {json.load(response)['status']}")
        except HTTPError as exc:
            raise SystemExit(f"{ticket['id']}: HTTP {exc.code} {exc.read().decode()}") from None
        except (URLError, TimeoutError) as exc:
            raise SystemExit(f"{ticket['id']}: {exc}") from None


if __name__ == "__main__":
    main()
