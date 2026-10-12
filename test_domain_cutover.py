from domain_cutover import Onboarding, onboard


class RecordingAPI:
    def __init__(self, status="verified"):
        self.calls = []
        self.status = status

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path == "/v1/dns/domain/add":
            return {"zone_id": "zone-42"}
        if path == "/v1/email/domain/verify":
            return {"verification": {"status": self.status}}
        return {}


def payload(passed=True, action="cutover"):
    return Onboarding.model_validate({
        "domain": "mail.example.com",
        "spf": {"record_type": "TXT", "name": "mail.example.com", "content": "v=spf1 include:example.com ~all"},
        "dkim": {"record_type": "CNAME", "name": "selector._domainkey.mail.example.com", "content": "selector.example.com"},
        "dmarc": {"record_type": "TXT", "name": "_dmarc.mail.example.com", "content": "v=DMARC1; p=none"},
        "build": {"build_id": "build-42", "passed": passed},
        "release": {"release_id": "release-42", "action": action},
    })


def test_cutover_waits_for_dns_verification_and_build():
    api = RecordingAPI("pending")
    assert onboard(payload(), api).action == "hold"
    assert api.calls[1][2]["json"]["zone_id"] == "zone-42"
    assert [call[2]["json"]["record_type"] for call in api.calls[1:4]] == ["TXT", "CNAME", "TXT"]
    assert onboard(payload(), RecordingAPI()).action == "cutover"
    blocked = RecordingAPI()
    assert onboard(payload(passed=False), blocked).action == "hold"
    assert blocked.calls == []


def test_rollback_does_not_change_dns():
    api = RecordingAPI()
    assert onboard(payload(action="rollback"), api).action == "rollback"
    assert api.calls == []
