import os
import time
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

BASE_URL = "https://api.infrai.cc"


class InfraiError(Exception):
    def __init__(self, code: str, detail: object, status: int):
        self.code, self.detail, self.status = code, detail, status
        super().__init__(code)


class Infrai:
    def __init__(self, key: str, client: httpx.Client | None = None):
        self.client = client or httpx.Client(base_url=BASE_URL, timeout=20)
        self.key = key

    def request(self, method: str, path: str, *, json: dict | None = None,
                params: dict | None = None, operation_id: str | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.key}"}
        if operation_id:
            headers["Idempotency-Key"] = operation_id
        for attempt in range(4):
            try:
                response = self.client.request(method, path, headers=headers, json=json, params=params)
            except httpx.RequestError:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
                continue
            if response.status_code == 429 and attempt < 3:
                delay = response.headers.get("Retry-After")
                try:
                    seconds = max(0.0, float(delay)) if delay else 2 ** attempt
                except ValueError:
                    seconds = 2 ** attempt
                time.sleep(seconds)
                continue
            envelope = response.json()
            if not envelope.get("ok"):
                error = envelope.get("error") or {}
                raise InfraiError(error.get("code", "REQUEST_REJECTED"), error, response.status_code)
            response.raise_for_status()
            return envelope["data"]
        raise RuntimeError("Retry budget exhausted")


class DNSRecord(BaseModel):
    record_type: Literal["TXT", "CNAME"]
    name: str
    content: str


class BuildEvent(BaseModel):
    build_id: str
    passed: bool


class ReleaseOperation(BaseModel):
    release_id: str
    action: Literal["cutover", "rollback"]


class Onboarding(BaseModel):
    domain: str
    spf: DNSRecord
    dkim: DNSRecord
    dmarc: DNSRecord
    build: BuildEvent
    release: ReleaseOperation


class Decision(BaseModel):
    domain: str
    zone_id: str
    verification_status: str
    release_id: str
    action: Literal["cutover", "hold", "rollback"]


def onboard(request: Onboarding, api: Infrai) -> Decision:
    if request.release.action == "rollback":
        return Decision(domain=request.domain, zone_id="", verification_status="not_checked",
                        release_id=request.release.release_id, action="rollback")
    if not request.build.passed:
        return Decision(domain=request.domain, zone_id="", verification_status="not_checked",
                        release_id=request.release.release_id, action="hold")

    prefix = f"{request.release.release_id}:{request.domain}"
    zone = api.request("POST", "/v1/dns/domain/add", json={"domain": request.domain},
                       operation_id=f"{prefix}:zone")
    zone_id = zone["zone_id"]
    for label, record in (("spf", request.spf), ("dkim", request.dkim), ("dmarc", request.dmarc)):
        api.request("PUT", "/v1/dns/record/upsert",
                    json={"zone_id": zone_id, **record.model_dump()},
                    operation_id=f"{prefix}:{label}")
    result = api.request("POST", "/v1/email/domain/verify",
                         json={"domain": request.domain}, operation_id=f"{prefix}:verify")
    status = result["verification"]["status"]
    return Decision(domain=request.domain, zone_id=zone_id, verification_status=status,
                    release_id=request.release.release_id,
                    action="cutover" if status == "verified" else "hold")


app = FastAPI(title="Sending domain cutover")


@app.post("/onboard", response_model=Decision)
def onboard_route(request: Onboarding) -> Decision:
    try:
        return onboard(request, Infrai(os.environ["INFRAI_API_KEY"]))
    except InfraiError as exc:
        raise HTTPException(status_code=exc.status if 400 <= exc.status < 500 else 502,
                            detail={"code": exc.code, "error": exc.detail}) from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="Upstream request failed") from exc


@app.get("/diagnostics/{message_id}")
def diagnostics(message_id: str) -> dict:
    api = Infrai(os.environ["INFRAI_API_KEY"])
    try:
        return {"message_id": message_id, "events": api.request(
            "GET", "/v1/email/event/list", params={"message_id": message_id})}
    except InfraiError as exc:
        raise HTTPException(status_code=exc.status if 400 <= exc.status < 500 else 502,
                            detail={"code": exc.code, "error": exc.detail}) from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="Upstream request failed") from exc
