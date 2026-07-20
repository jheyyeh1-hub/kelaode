# ETF9 replacement snapshot-v2 preregistration

Status: **preregistered infrastructure only; no acquisition has been attempted**.

## Frozen acquisition

The authoritative machine-readable acquisition configuration is
`configs/validation/etf9_snapshot_v2_acquisition.json`. It freezes AKShare/Eastmoney,
`fund_etf_hist_em`, daily frequency, `qfq`, and requested range 2005-01-01 through
2026-07-17. The ordered universe is 510300, 510500, 159915, 512100, 512880,
512480, 518880, 513100, and 511010.

The timeout is 30 seconds. An acquisition has three retries after the initial
request, with frozen delays of 1, 2, and 4 seconds. There is no fallback provider,
alternate endpoint, symbol substitution, mixed adjustment, or partial snapshot.
If any symbol ultimately fails, the complete attempt fails and every artifact
from that attempt is discarded.

## Canonical artifacts

Each successful response will be normalized before the maintained serializer is
called. CSV is UTF-8 with LF endings; its fixed ordered header is
`date,open,high,low,close,volume`. Dates are ascending `YYYY-MM-DD`, duplicate
dates and unsupported missing values are errors, and all numeric cells use
locale-independent fixed `.8f` formatting. No index is emitted.

The manifest binds every file's bytes and acquisition metadata to hashes of this
protocol, the acquisition configuration, and the source tree. Its canonical
identity additionally binds the ordered universe and ordered CSV hashes. An
archive is a deterministic uncompressed POSIX tar with the nine CSVs in universe
order followed by `manifest.json`.

This protocol does not authorize a network request, strategy run, performance
claim, release, or publication. Those require separate reviewed work.
