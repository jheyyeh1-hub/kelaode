# SIT real-market validation report (2026-07-19 retry)

## 1. Executive conclusion

* `execution_status = COMPLETED`
* `strategy_judgment = CONDITIONAL`

The complete preregistered data freeze succeeded and the formal fixed-selection and walk-forward
experiments completed. The judgment above was returned by `evaluate_strategy_judgment(...)`, not
assigned manually. It is research validation, not an investment recommendation or a claim of
future performance. The conservative diagnostic inputs described in section 8 trigger
`CONDITIONAL` even though the primary return, drawdown, and moderate-cost clauses pass.

## 2. Preregistration anchor

The attempt began from merged latest-main commit
`a3c06b76bc5c8b8cc902652b87908278eecc8c99` (PR #14). Before any provider request, the protected
files had these SHA-256 values:

| Frozen file | SHA-256 |
|---|---|
| `docs/validation/sit_real_market_protocol.md` | `70b9e0c50dc7bae2699d1c083cf36bd06351c8f4c3e3b51b42a6dfd045c45b86` |
| `configs/validation/sit_real_market_fixed.json` | `0bc6c7974021ee62ad3abbdaef48c7d2236990210546a92b9b403e5738066aba` |
| `configs/validation/sit_real_market_walk_forward.json` | `297988f0a42335e1718113cdf36af85519041888ec8f9478fec93b4222f9a8a3` |
| `configs/validation/sit_validation_policy.json` | `a3ebc47943b38ef8d246a6691817e8fd58c6e72ecbf8140441b48590e54eb00e` |

The same hashes were reproduced after publication and execution. No frozen protocol, policy,
universe, parameter-grid, date, cost, benchmark, objective, eligibility, or judgment value changed.

## 3. Data freeze and immutable identity

The successful request started `2026-07-19T10:45:03Z` and finished `2026-07-19T10:45:24Z`. It used
only AKShare/Eastmoney `fund_etf_hist_em`, daily `qfq`, 2005-01-01--2026-07-17, and the exact nine
symbols. There was no fallback, alternate endpoint, substitution, removal, or mixed adjustment.
A new staging directory was used; the earlier failed partial directory was not reused. The staging
manifest was published at `data/snapshots/sit-20260719/manifest.json` only after all entries
succeeded.

* Snapshot manifest file SHA-256: `682e83a62e8acc3d4ef3a45c32174a6eff2e668df101bb12e02104d847141643`.
* Canonical snapshot identity: `16ecae299c7944302c0bffe3688bf9bdb2b931012a82d3dd47c79c36778fabfe`.
* Common range: 2019-06-12--2026-07-17.
* Both complete validations passed. They verified hashes, row counts, unique strictly ordered dates,
  finite positive OHLC and nonnegative volume, OHLC relationships, exact universe order, and one
  `qfq` convention. The quality scan emitted no adjacent-close-jump warnings.

| Symbol | Exchange/class | Official listing | Actual range | Rows | CSV SHA-256 |
|---|---|---:|---|---:|---|
| 510300 | SSE / CSI 300 broad equity | 2012-05-28 | 2012-05-28--2026-07-17 | 3436 | `2cb9b8ffef2d2de1b42cf879f203bbcd80f84804bbf01bf30565da201e4b6408` |
| 510500 | SSE / CSI 500 broad equity | 2013-03-15 | 2013-03-15--2026-07-17 | 3239 | `71c7063e984e0f74e257145d57935e6463432bbc135819553ee4fe8388e9ac57` |
| 159915 | SZSE / ChiNext broad equity | 2011-12-09 | 2011-12-09--2026-07-17 | 3544 | `6139967d17fecb4cb84ac8fa1034602fc61d3edfb04e43d5b3e662ffb25fe570` |
| 512100 | SSE / CSI 1000 broad equity | 2017-08-25 | 2016-11-04--2026-07-17 | 2355 | `af5b5fbb915d20d10a74c9db7f23e383f5d20eac1f2dde1839be5b0889f72c93` |
| 512880 | SSE / securities industry equity | 2016-08-08 | 2016-08-08--2026-07-17 | 2413 | `8410580b3c10ffdf780532fee0bd8b4220545e7c3aa11e18084ea412f09a9acd` |
| 512480 | SSE / semiconductor industry equity | 2019-06-12 | 2019-06-12--2026-07-17 | 1722 | `e8d9516076913cd0a145e4e086d4d398958825947d61ba5dc7326402a88c86ef` |
| 518880 | SSE / physical gold commodity | 2013-07-29 | 2013-07-29--2026-07-17 | 3153 | `1bca5f49e7dbe7f8fcddd833d6065e5cf7d1e0e184c42942aa8eb3394b754086` |
| 513100 | SSE / NASDAQ-100 cross-border equity | 2013-05-15 | 2013-05-15--2026-07-17 | 3202 | `d4d0d2c9e0babe70e428a644b70abe9ed54f514760a4d71936599e26172a3c6d` |
| 511010 | SSE / government bond | 2013-03-25 | 2013-03-25--2026-07-17 | 3235 | `17275886de8377d5ad7ca87dc53fa248966afef2a021987f76abe914b3f94613` |

The provider begins 512100 observations before the frozen official listing date. This was retained
as an explicit diagnostic rather than silently changing metadata or data. No formal position
predated the official listing date, as independently audited.

## 4. Fixed selection and frozen test

* Fixed experiment ID: `135d2262f8a2ecbd354eb68a80906557c671e64064c33b913ae3cf5cfef055cb`.
* Selected candidate ID: `6c33b6f3678b4794b7d2a288ed0de6dffe2920a7b3a4d8c4f07e7f400e281052`.
* Selected parameters: momentum 126, top-k 3, trend 126, volatility 63.
* Frozen-test child ID: `84ced0399ac60fe44d209faf1927fa2e35b6a5ed4bfcf7f9e95311caa457e652`.
* Validation: total return `0.008904678140710498`, Sharpe `0.0998354956538619`, maximum drawdown
  `-0.15825769289200498`, turnover `14.71925503122003`, 91 fills.
* Frozen test: total return `0.6133677934147121`, CAGR `0.15140120956661973`, annualized volatility
  `0.15206534486035`, Sharpe `1.0038687475110417`, Sortino `1.385055464191464`, Calmar
  `0.8302799593241411`, maximum drawdown `-0.1823495892757213`, maximum drawdown duration 230,
  turnover `9.660430549076484`, and 60 fills.
* Primary benchmark excess total return: `0.6133677934147121`; tracking error
  `0.1073520447276568`; information ratio `0.7076847215342064`; alpha `0.07597140187921947`;
  beta and correlation `0.0`; active drawdown `-0.18234958927572154`.
* Closed-loop total returns: base `0.6133677934147121`, moderate `0.5996080088463458`, severe
  `0.4042692027613799`. Fixed-path final equities were CNY `161336.7793414712`,
  `160478.8318767846`, and `158634.52465613803`; base reconciliation passed within CNY 0.000001.

The second identical fixed command returned the same experiment directory without re-execution,
proving sealed cache reuse.

## 5. Walk-forward

Walk-forward experiment ID:
`8df3f6b0ee01068041215d1db755b4b46fdd30f614c0d0a1aa86c55cdc9efa3d`. Nine nonoverlapping OOS
folds were produced. Every fold selected momentum 126, top-k 2, no trend filter, and no volatility
weighting. Stitched OOS total return was `0.2087338448268714`, CAGR `0.04306615849496187`,
annualized volatility `0.17214046812437064`, Sharpe `0.3313490133072057`, Sortino
`0.46040549149367394`, Calmar `0.2280383092638999`, maximum drawdown `-0.18885492807755855`, and
maximum drawdown duration 500. The second identical command returned the same experiment directory,
proving sealed cache reuse.

## 6. Independent audits

The fixed audit passed 28 distinct checks over one frozen-test bundle. The walk-forward audit passed
33 distinct checks over nine bundles. Together these covered recursive artifact hashes and
identities, exact data hashes and qfq consistency, selected-only tests, accounting, trades/fills,
next-open and warm-up boundaries, official listing dates, benchmark alignment and reconstruction,
cost replay and monotonicity, fold identities, nonoverlapping OOS dates, and stitched equity,
drawdown and metrics reconstruction.

The initial audit exposed a representation-only defect: CSV values `5` and `5.0` compared unequal
as strings. The auditor now compares quantity as an integer and price/commission numerically while
retaining exact categorical matching and tolerance checks. A regression test covers equivalent
numeric formatting. No result artifact or frozen value was changed.

## 7. Mechanical judgment

All exact inputs and frozen thresholds supplied to `evaluate_strategy_judgment(...)` are persisted
in `sit_real_market_judgment_inputs.json`. The function returned `CONDITIONAL`. The moderate equity
ratio was `0.9914713900794787`; the severe closed-loop equity ratio was `0.8703962038248126`, below
the frozen 0.90 conditional threshold. Therefore PASS is unavailable.

## 8. Limitations

* The sealed runner does not emit ETF/year P&L attribution or one-factor frozen-test diagnostic
  bundles. To avoid inventing favorable evidence or testing unselected candidates formally, the
  judgment call conservatively supplied contribution share `1.0` and neighbor reversal `true`.
  Either independently triggers CONDITIONAL; these are explicit conservative bounds, not observed
  diagnostic estimates.
* CSV market data and generated result bundles remain Git-ignored. The small immutable manifest is
  committed, while the report records every identity and exact artifact-relative location.
* AKShare/Eastmoney data and listing metadata can contain the noted 512100 pre-official-listing
  discrepancy. It was not hidden, substituted, or used to change the protocol.
* GitHub CI is remote evidence and must be observed on the pull request; it cannot be asserted by
  local execution.

## 9. Reproduction commands

```bash
PYTHONPATH=src python -m kelaode.data_cli download --symbols 510300,510500,159915,512100,512880,512480,518880,513100,511010 --start 2005-01-01 --end 2026-07-17 --output <new-staging-directory> --adjust qfq --retries 2 --format csv
PYTHONPATH=src python -m kelaode.experiment_cli grid-search --config configs/validation/sit_real_market_fixed.json
PYTHONPATH=src python -m kelaode.experiment_cli walk-forward --config configs/validation/sit_real_market_walk_forward.json
PYTHONPATH=src python -m kelaode.validation_audit --artifacts results/sit-real-market-fixed/135d2262f8a2ecbd354eb68a80906557c671e64064c33b913ae3cf5cfef055cb
PYTHONPATH=src python -m kelaode.validation_audit --artifacts results/sit-real-market-walk-forward/8df3f6b0ee01068041215d1db755b4b46fdd30f614c0d0a1aa86c55cdc9efa3d
```
