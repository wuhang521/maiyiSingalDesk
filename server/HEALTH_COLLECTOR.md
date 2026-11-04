# Public health RSS collector

`app/health_collector.py` is a standalone transport adapter for the existing
private health business generator. Run it with the Task Python environment,
which already provides HTTPX and its certifi trust store. It adds no dependency
and does not modify or distribute the existing private generator.

```text
/opt/signaldesk-task/.venv/bin/python /opt/signaldesk-task/app/health_collector.py \
  --collector-root /opt/signaldesk \
  --date YYYY-MM-DD \
  --output-dir <existing-private-output-directory> \
  --max-items 100
```

The caller must supply an existing, real output directory. On Linux it must be
owned by the executing identity and mode0700. The collector atomically writes
`YYYY-MM-DD.md` and `summary.json` as mode0600. The Markdown is the original
`generate_health_business_markdown` result, including its existing frontmatter;
the adapter does not invent article text or change its format. An unsuccessful
collection writes only a safe failure summary and leaves an existing Markdown
untouched. The caller must inspect the exit code before consuming output.

`--date` selects the report/file date. Source freshness is evaluated against the
actual UTC runtime clock, rather than treating old RSS entries as new. At least
one genuine selected item is required; `--max-items` is a cap in1..100 and does
not require or manufacture that many articles.

The only network targets are the exact feeds advertised by these publishers:

| Publisher | Official discovery | Fixed RSS |
| --- | --- | --- |
| PR Newswire health releases | https://www.prnewswire.com/rss/ | https://www.prnewswire.com/rss/health-latest-news/health-latest-news-list.rss |
| Healthcare Dive | https://www.healthcaredive.com/ | https://www.healthcaredive.com/feeds/news/ |

PR Newswire is attributed as corporate press releases. A release is not
presented as independent reporting. The adapter requests RSS only; it never
requests article pages or extracts full article content. It ignores RSS
description/content fields and preserves each accepted item link and real
publication date. A neutral empty query avoids assigning invented search intent.

Requests use strict TLS with the existing HTTPX/certifi trust store, no proxy
environment, redirects, authentication or cookies. Each operation times out
after5seconds; the Linux main-thread network phase also has a25second total
deadline. The decoded body cap is1MiB per provider and2MiB overall. Compressed
responses, DTD/entities and more than200RSS items per provider are rejected.
The fixed UTF-8 feeds do not require UTF-16/32; NUL-bearing XML is rejected
before parsing so an alternate encoding cannot hide an entity declaration.
Missing/naive dates, future dates, non-HTTPS or credential-bearing links and
oversized fields are rejected rather than replaced with current values. Links
must use the default HTTPS port or443. Local/single-label hostnames, non-global
literal IPs and noncanonical numeric IP spellings are rejected without DNS
resolution; only syntactically valid multi-label DNS names or global literal
addresses are accepted.

After transport normalization, the adapter imports only the existing local
generator module and calls its `NewsItem`, `dedupe_news_items`,
`is_relevant_health_business_item` and `generate_health_business_markdown` APIs.
It never invokes its legacy collector, fetcher or CLI. Freshness is applied
before deduplication: first the previous24hours, then the previous72hours only
when the first window yields no selected article. This prevents an older copy
from suppressing a fresh copy from another provider. The original business
filtering, selection and Markdown formatting remain with the private generator.

The standalone CLI removes nonessential environment variables before loading
private policy. Its caller must also pass the existing restricted collector
environment, never Task cloud credentials, database URLs or API keys. No `.env`
file, database, notification service or user profile is read. Standard output
contains a single JSON summary of provider states/counts, selected count and
SHA256. Legacy output is suppressed; exception messages, article text and URLs
are not emitted. `summary.json` carries the same metadata.

The fixed feeds are finite rolling windows and do not guarantee complete
domestic/global coverage or a qualifying item every day. A provider failure can
be tolerated when the other yields genuine qualified news; an empty result must
remain a failure for the caller's delayed-status policy. Google/Bing, mirrors,
region changes, login, paid services and security-page workarounds are absent.

Synthetic tests are in `tests/test_health_collector.py`. The delivery records
separate those tests, actual service-identity RSS probes, the synthetic real
legacy-loader contract and the later live `Worker.prepare` acceptance. This
source guide alone does not assert production activation or actual delivery.

## Caller receipt contract

The collector remains at source SHA
`04e179b61df6d86e3c714c49fca297e9517fc01b8df674c083743d0fa87cf5be`.
The caller Worker at SHA
`91e5f649ff6773b08b0ba40e92656ed5457b42c811ac873c2a1d3302f1a3130d`
adds only a receipt-matching correction in `Ledger.matches`; it does not change
this collector, its output format, the frozen packet or the stored fingerprint.

The upstream normal-text handler trims edge whitespace with Go
[`strings.TrimSpace`](https://pkg.go.dev/strings#TrimSpace); see the
[ntfy handler](https://github.com/binwiederhier/ntfy/blob/v2.29.0/server/server.go#L1376-L1386)
and [JSON-to-body conversion](https://github.com/binwiederhier/ntfy/blob/v2.29.0/server/server.go#L2100-L2117).
The caller accepts an exact original fingerprint first. Its narrow fallback
requires the same title, a still-valid original stored fingerprint, and a
receipt body equal to the original body with only the explicitly enumerated Go
edge-whitespace characters removed. It does not relax interior whitespace,
title, or unrelated fields, and an unconfirmed send remains unknown rather
than being blindly resent.

The final Worker and unchanged collector also passed a fresh real-service-UID
RSS prepare-only run: three genuine selected articles in one packet, real
clock, unchanged private configuration, mode0600 files/mode0700 directories,
and zero ingest/publish/cache calls. This proof is separate from actual timer,
client receipt and Windows system-notification acceptance.
