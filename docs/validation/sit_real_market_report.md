# SIT real-market validation report (2026-07-19 retry)

## 1. Executive conclusion

* `execution_status = COMPLETED`
* `strategy_judgment = CONDITIONAL`
* `judgment_status = PROVISIONAL`
* `diagnostic_status = INCOMPLETE`

The immutable data freeze succeeded and the corrected formal fixed-selection and walk-forward
experiments completed. The first completed run from commit `bbe789a` is **invalidated and must not be
used as formal evidence**: its primary equal-weight buy-and-hold benchmark remained entirely in cash.
The runner supplied an execution boundary after warm-up began, while `EqualWeightBuyAndHold` emitted
its sole target only at global index zero; that warm-up target was discarded and every eligible call
then held cash. Its excess return equaled strategy return and its beta/correlation were zero. Those
identities and metrics are superseded by the corrected run below. The judgment above was returned by `evaluate_strategy_judgment(...)`, not
assigned manually. It is research validation, not an investment recommendation or a claim of
future performance. Missing diagnostics are explicitly incomplete rather than replaced with asserted
values. Corrected benchmark excess return is negative, so the frozen PASS clauses do not all pass.

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

* Fixed experiment ID: `6d7ee7cfe8707ae5f9d4ea5de39e593f2625b709f3ffb9a3ee7b64374b861f83`.
* Selected candidate ID: `df2db91542631bd2e79f9185ecb1b00a2f728bd3d8fa016c12104c0595542090`.
* Selected parameters: momentum 126, top-k 3, trend 126, volatility 63.
* Frozen-test child ID: `2a134c77c650d692256fb5e6731427b0510f370a7a3bd4ab6e4fe9ec42e59cba`.
* Validation: total return `0.008904678140710498`, Sharpe `0.0998354956538619`, maximum drawdown
  `-0.15825769289200498`, turnover `14.71925503122003`, 91 fills.
* Frozen test: total return `0.6133677934147121`, CAGR `0.15140120956661973`, annualized volatility
  `0.15206534486035`, Sharpe `1.0038687475110417`, Sortino `1.385055464191464`, Calmar
  `0.8302799593241411`, maximum drawdown `-0.1823495892757213`, maximum drawdown duration 230,
  turnover `9.660430549076484`, and 60 fills.
* Corrected primary benchmark total return: `0.6403372535000003`; annualized volatility
  `0.11943413109550455`. Corrected frozen-test excess total return: `-0.02696946008528811`;
  tracking error `0.09933678617504374`; information ratio `-0.0378890073288071`; alpha
  `0.03147363789246453`; beta `0.5580694403666174`; correlation `0.62087814787537`; active
  drawdown `-0.22062305450564346`.
* Closed-loop total returns: base `0.6133677934147121`, moderate `0.5996080088463458`, severe
  `0.4042692027613799`. Fixed-path final equities were CNY `161336.7793414712`,
  `160478.8318767846`, and `158634.52465613803`; base reconciliation passed within CNY 0.000001.

The second identical fixed command returned the same experiment directory without re-execution,
proving sealed cache reuse.

## 5. Walk-forward

Walk-forward experiment ID:
`9d4855b06c7156505745980cebb7cc2a10adb93d061827c8a76fe48f3af2a98d`. Nine nonoverlapping OOS
folds were produced. Every fold selected momentum 126, top-k 2, no trend filter, and no volatility
weighting. Stitched OOS total return was `0.2087338448268714`, CAGR `0.04306615849496187`,
annualized volatility `0.17214046812437064`, Sharpe `0.3313490133072057`, Sortino
`0.46040549149367394`, Calmar `0.2280383092638999`, maximum drawdown `-0.18885492807755855`, and
maximum drawdown duration 500. The second identical command returned the same experiment directory,
proving sealed cache reuse.

### Walk-forward sealed identities

| Fold | Fold ID | Selected candidate ID | Test-child ID |
|---:|---|---|---|
| 0 | `19f7f4e4d1a87ef01318dbe1f6831ad38874a816233872fdb02947b03cd012a5` | `b9927b4ad47a7808d5affb10c8c8ac2511d13a5cbb3987873678b89e3dda7fbe` | `862e22010b35b4ab16b79b67c366981d80b08f80e3366b8580d19935914f5be8` |
| 1 | `b185aea236b15160d8d4ecdad51df82d469ca7464f99620bf024eefbd521bda2` | `8dafbb8bedcc52a3365ac2475436b512a560c023f864ccf9316ffd40dbacdf82` | `60725c191e0c1895349f58b8d8f8b4a46254a568b9da6cc821b2681f1af3ea0b` |
| 2 | `3145295feeaa0efffac29c6f3dea9d240e821f32a9ab486d164b29fc8135b782` | `d0d6ae93ab0c7bd8a070b7d415c3f5143d82583662cf3fc35ffa1f660b714872` | `8533427790a0567086531dff35e92a451644a013f1f65950525549f99e9532e8` |
| 3 | `dfe92b3f10cd959e8724912498e2bf80830d2c49372bc2f8dc77338753889af5` | `c2af12d04a5eb8df62005351c02d70c37ea0be747b30cf3aa36215b2e46e2bb2` | `abbb429d1662fb6a1fb7e7c29e0be7d96340948e8a398ffc64e9ae7d848213c7` |
| 4 | `5ef89828468ba2a2b121c84fb547fe5b496f74dd9d74215e434d40d18b925c68` | `63227b7a4ca11438ca47c411bcfc45f7d3fb71b44c5097c6573447a3deb290a7` | `351a11e382e21b18229ca5973fbfac844eb2274d3bd2a0e44b603dd8a45f20a5` |
| 5 | `2ff1f1c48403043b2a864608ba9dee34a048b80542df84b3acaeb2d48b34a695` | `a91c9c8c08f278b32e479d528e1c8dc067c20d15615cb72dc79224688170e1b9` | `0f32e7065f888df45070d9d47a88c63316685deacd59a16afa6a9bd63b19e354` |
| 6 | `88f80a5a28bad1b22f8f348edb16e6e146024c2eb3aa42d8c8eaa031c27be98a` | `24e1038c7abdd4d1c2ce67bed86767b3abfaf1f00d12c38ea9f9886336d0e0e3` | `f9bc1d8f38c4f18f71b7b5f90d0d33f4b2ab8879702cfd21434a06e8a3c1ce36` |
| 7 | `3c45d06a15ac81627f64240596fb848c8a7799e6de0d8d1ce8636e5099cee9e8` | `d2fa63c3b7355c1e392e57f5473499fd55d3ec53439397f48a404bd9e1c86a9b` | `59bc7490287a2811424353435ee3199e6b1a986d272d22350242851d7bab7fc7` |
| 8 | `a8aca59e700952b1e218aabc842eef57c824929f70625a15e2514360c4648b74` | `6250dcc30667b5328dfe183aa8b60815075a671e4d3f938d35af81fd11e864c3` | `88af287cb8c71a059712ae9cb267c996d09e1b9e0f12a54ff1da1f32577466b0` |

## 6. Independent audits

The fixed audit passed 36 distinct checks over one frozen-test bundle. The walk-forward audit passed
41 distinct checks over nine bundles. Together these covered recursive artifact hashes and
identities, exact data hashes and qfq consistency, selected-only tests, accounting, trades/fills,
next-open and warm-up boundaries, official listing dates, benchmark alignment and reconstruction,
cost replay and monotonicity, fold identities, nonoverlapping OOS dates, and stitched equity,
drawdown and metrics reconstruction.

The initial audit exposed a representation-only defect: CSV values `5` and `5.0` compared unequal
as strings. The auditor now compares quantity as an integer and price/commission numerically while
retaining exact categorical matching and tolerance checks. A regression test covers equivalent
numeric formatting. No result artifact or frozen value was changed.

## 6.1 Corrected source and benchmark evidence

The corrected artifacts use source commit `ca9d491f714aa05ef771d887bd407fd27b3103d6` and source-tree
SHA-256 `bd653e72fd3c7ee46e84bfc1cb20cfb294262e7cfa0af5b24cb501fae4521fef`. The benchmark now emits
its equal nine-way target on the first execution-eligible signal date, executes strictly later, and
holds without periodic rebalancing. Sealed benchmark cash, orders, fills, positions, marks, weights,
and equity support independent checks for nonconstant equity, actual investment, no warm-up activity,
equal initial targets, accounting, total return, relative metrics, and strategy excess return.

## 7. Mechanical judgment

All supplied inputs and frozen thresholds are persisted in `sit_real_market_judgment_inputs.json`.
`evaluate_strategy_judgment(...)` returned `CONDITIONAL`. The result is **provisional**, because
`diagnostic_status = INCOMPLETE`: ETF/calendar-year positive-P&L concentration and adjacent
one-factor excess-return sign reversal were not measured. No placeholder observation is supplied.
Independently, corrected frozen-test excess return is negative and the severe closed-loop equity
ratio is `0.8703962038248126`, below the frozen 0.90 conditional threshold, so PASS is unavailable.

## 8. Limitations

* The sealed runner does not emit ETF/year P&L attribution or one-factor frozen-test diagnostic
  bundles. These diagnostics are explicitly `INCOMPLETE`; no concentration value or reversal flag
  is presented as measured evidence.
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
PYTHONPATH=src python -m kelaode.validation_audit --artifacts results/sit-real-market-fixed/6d7ee7cfe8707ae5f9d4ea5de39e593f2625b709f3ffb9a3ee7b64374b861f83
PYTHONPATH=src python -m kelaode.validation_audit --artifacts results/sit-real-market-walk-forward/9d4855b06c7156505745980cebb7cc2a10adb93d061827c8a76fe48f3af2a98d
```
