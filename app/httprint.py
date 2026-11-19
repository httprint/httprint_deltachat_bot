import logging
import os
from typing import BinaryIO

import httpx

logger = logging.getLogger(__name__)


class Httprint:
    def __init__(self, host: str):
        if not host.strip():
            raise ValueError("HTTPRINT_HOST must not be empty")
        self.host = host.rstrip("/")
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    @staticmethod
    def parseopt(optstr: str) -> dict[str, str | int]:
        options: dict[str, str | int] = {}
        for opt in optstr.lower().split():
            match opt:
                case "single" | "one":
                    options["sides"] = "one-sided"
                case "long" | "2l" | "2long" | "twol" | "duplex" | "two":
                    options["sides"] = "two-sided-long-edge"
                case "short" | "2s" | "2short" | "twos":
                    options["sides"] = "two-sided-short-edge"
                case "a3" | "a4" | "a5":
                    options["media"] = opt.upper()
                case "mono" | "gray" | "black" | "bw":
                    options["color"] = "false"
                case "color" | "col" | "rgb" | "cmyk":
                    options["color"] = "true"
                case _:
                    if opt.isnumeric():
                        options["copies"] = int(opt)
        return options

    def upload(self, filename: str, file_data: BinaryIO, text: str) -> str:
        try:
            file_size = os.fstat(file_data.fileno()).st_size
            logger.debug("HTTP POST /api/upload started (%d file bytes)", file_size)
            response = httpx.post(
                f"{self.host}/api/upload",
                files={"file": (filename, file_data)},
                params=self.parseopt(text),
                timeout=30.0,
            )
            logger.debug(
                "HTTP POST /api/upload returned %d (%d file bytes)",
                response.status_code,
                file_size,
            )
            if response.is_error:
                try:
                    error_body = response.json()
                except ValueError:
                    error_body = None
                detail = (
                    error_body.get("message") or error_body.get("error")
                    if isinstance(error_body, dict)
                    else None
                )
                if not isinstance(detail, str) or not detail.strip():
                    detail = response.text.strip()[:1000]
                if not detail:
                    detail = "The server did not provide any details."
                logger.error(
                    "Upload API rejected %s with HTTP %s: %s",
                    filename,
                    response.status_code,
                    detail,
                )
                return f"❌ {detail}"

            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Unexpected upload response format")
            icon = "❌" if result.get("error") else "✅"
            return f"{icon} {result.get('message') or 'Upload completed'}"
        except httpx.HTTPError as error:
            logger.warning("HTTP POST /api/upload failed: %s", error)
        except (OSError, ValueError) as error:
            logger.warning("Upload failed: %s", error)
        return (
            "🛑 An error occurred while downloading or uploading the "
            "attachment. Please try again later."
        )
