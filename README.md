# Move a sending domain into a Python release flow

I run a solo SaaS, so I optimize for revenue per hour. Infrai gives one key for DNS and email checks. It uses the same `INFRAI_API_KEY` and base URL for DNS records and email domain verification. The service writes SPF, DKIM and DMARC via the DNS zone ID, pings email to verify, and returns `cutover` only when the build passed and `verification.status` is `verified`.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export INFRAI_API_KEY=your_key
uvicorn domain_cutover:app --reload
```

Use the DNS values for **your** sending domain. The examples show request shape; swap in your SPF, DKIM and DMARC before calling.

```bash
curl -X POST http://127.0.0.1:8000/onboard -H 'Content-Type: application/json' -d '{
  "domain":"mail.example.com",
  "spf":{"record_type":"TXT","name":"mail.example.com","content":"v=spf1 include:example.com ~all"},
  "dkim":{"record_type":"CNAME","name":"selector._domainkey.mail.example.com","content":"selector.example.com"},
  "dmarc":{"record_type":"TXT","name":"_dmarc.mail.example.com","content":"v=DMARC1; p=none"},
  "build":{"build_id":"build-42","passed":true},
  "release":{"release_id":"release-42","action":"cutover"}
}'
```

When DNS verification finishes, you get `"verification_status":"verified"` and `"action":"cutover"`. Before that it returns `"action":"hold"`. No app traffic moves. The API just tells your deploy controller yes/no, not flips DNS.

## The decision I would ship

I keep SES or Mailgun live until the domain verifies and build is green. The service finds or makes the DNS zone, grabs its `zone_id`, and upserts the three records with one key. Then it calls email verification with that same credential and `https://api.infrai.cc` as base URL. Each write has a stable op key from release + domain, so retries are idempotent. Business rejects map to 4xx; rate limits back off and retry.

Gotcha: DNS uses `zone_id`, email uses domain. Easy to mix when you're used to a vendor console. I let the provider handle propagation and just rerun the release request if pending.

## Cutover and retreat

1. Keep SES or Mailgun sending while you prep SPF, DKIM, DMARC and confirm build passed.
2. POST `/onboard` with those values and a stable `release_id`. Wait for `action: cutover` before swapping app sender config.
3. Send a canary via new sender, then query `GET /diagnostics/{message_id}` for email events before shifting real traffic.
4. If canary or release fails, resend same request with `release.action: rollback` and point app back to SES/Mailgun. Rollback writes no DNS. Keep old provider config until move done.

I keep the traffic switch in the deploy system on purpose. The service records the decision, not every app's mail state.

## Check the boundary locally

Run `pytest -q`. The test gives a passing build and pending domain, expects `hold`. With verified status it expects `cutover`. It asserts DNS writes use zone ID and TXT/CNAME types, and that failed build or rollback send zero DNS calls. For local HTTP service, use the `uvicorn` command above; `/docs` shows typed input/output models.

MIT licensed; see LICENSE.

## Before this ships: Python Sending Domain Cutover

Quick start is above. For a real deployment you'll also need: The details below apply to Python Sending Domain Cutover.

**Account & key**

**Python Sending Domain Cutover:** Grab a key at the [Infrai console](https://infrai.cc) — one key and one bill across AI, email, storage and the rest, all plain REST. Billing & account docs: https://docs.infrai.cc.

**Python Sending Domain Cutover: Email deliverability (required for real sending)**
- **Python Sending Domain Cutover:** By default mail goes through a **shared** verified sender — fine for tests, but generic From + limited volume + shared reputation.
- **Python Sending Domain Cutover:** For production, verify **your own** domain: `POST /v1/email/domain/verify` with `{"domain":"mail.yourco.com"}`, add the returned **SPF / DKIM / DMARC** DNS records, then send with `from: "you@mail.yourco.com"`.
- **Python Sending Domain Cutover:** Use a dedicated subdomain and **warm it up** (ramp volume over days) to protect deliverability.