# GiGO週間雨予報 Discord通知

GitHub Actionsで毎日、その日から1週間分の雨予報を取得し、Discord webhookへ文字列で送信します。

毎回GiGO店舗をスクレイピングしません。通常の通知では、`data/gigo_stores.csv` に保存済みの店舗名・都道府県・住所・座標を使います。店舗一覧の更新は、初回と店舗追加時だけ手動Actionで実行します。

Discordに表示される内容は次の形式です。

```text
【GiGO週間雨予報 / Open-Meteo Single Runs jma_gsm run=2026-06-07T00:00 / 2026-06-08から7日】

2026-06-08(月)
GiGO調布/東京都 75% / 7mm
GiGO町田/東京都 40% / 1mm

2026-06-09(火)
GiGO調布/東京都 60% / 3mm
GiGO町田/東京都 30% / 0.5mm
```

## 構成

```text
.github/workflows/gigo-rain-discord.yml      # 毎日の1週間分通知。スクレイピングしない
.github/workflows/update-gigo-stores.yml    # 手動の店舗CSV更新。初回と店舗追加時だけ使う
scripts/gigo_rain_discord.py                # CLI入口
scripts/weather.py                          # Open-Meteo Forecast / Single Runs
scripts/jma_weekly.py                       # 気象庁府県週間天気予報JSONの取得と抽出
data/gigo_stores.csv                        # 静的店舗CSV
```

## 初回設定

1. GitHubリポジトリの `Settings` → `Secrets and variables` → `Actions` → `New repository secret` で、Repository secretとして `DISCORD_WEBHOOK_URL` を登録します。
2. `Actions` タブから `Update static GiGO store CSV` を手動実行します。
3. `data/gigo_stores.csv` に店舗名、都道府県、住所、緯度、経度が書き込まれてコミットされます。
4. 以後は `Send daily GiGO rain forecast to Discord` が毎日17:35 JSTに動きます。

## 予報ソース

`forecast_source` で次の3つを選べます。

```text
both
jma_weekly
open_meteo_single_run
```

`both` は、気象庁府県週間天気予報方式とOpen-Meteo Single Runs方式の両方を送ります。

### jma_weekly

気象庁の公式bosai forecast JSONから、府県週間天気予報相当の降水確率を取得します。GitHub Actionsを動かした時点の最新予報を送信する方式です。

この方式は都道府県代表の府県予報区コードに寄せています。そのため、北海道、東京都離島、鹿児島県奄美地方など、気象庁が細分している地域は厳密には代表区域扱いになります。

### open_meteo_single_run

Open-Meteo Single Runs APIから、指定したUTC初期化時刻のモデルrunを取得します。既定では、予報開始日以前の直近日曜日 `00:00 UTC` の `jma_gsm` runを使います。

`jma_gsm` では `precipitation_probability_max` が未定義で返る場合があるため、Open-Meteo方式の表示には `precipitation_sum` の日降水量も含めます。

例として、2026年6月9日(火)から7日分を、2026年6月7日(日)時点の予報で見たい場合は、手動実行で次を指定します。

```text
forecast_start_date = 2026-06-09
open_meteo_run = 2026-06-07T00:00
open_meteo_model = jma_gsm
```

## 毎日実行で行うこと

1. `data/gigo_stores.csv` を読む。
2. 選択した予報ソースで、実行日から7日分の予報を取得する。
3. 各日ごとに、気象庁方式は降水確率順、Open-Meteo方式は降水確率と日降水量順で店舗を並べる。
4. Discord webhookへ通常の文字列として送信する。

## 環境変数

| 変数名 | 内容 | 既定値 |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | Discord webhook URL。GitHub Secretsに登録します。 | なし |
| `GIGO_STORES_CSV` | 静的店舗CSVのパス | `data/gigo_stores.csv` |
| `FORECAST_SOURCE` | `both` / `jma_weekly` / `open_meteo_single_run` | `both` |
| `FORECAST_START_DATE` | 予報開始日。例: `2026-06-09` | 実行日のJST日付 |
| `WEEK_DAYS` | 何日分送るか | `7` |
| `MIN_POP_PERCENT` | Discordへ載せる最低降水確率 | `0` |
| `TOP_N_PER_DAY` | 各日で表示する上位店舗数 | `10` |
| `OPEN_METEO_BATCH_SIZE` | Open-Meteoへまとめて問い合わせる店舗数 | `50` |
| `OPEN_METEO_RUN` | Single RunsのUTC初期化時刻。例: `2026-06-07T00:00` | 予報開始日以前の直近日曜00UTC |
| `OPEN_METEO_RUN_HOUR_UTC` | `OPEN_METEO_RUN`未指定時の日曜run時刻 | `0` |
| `OPEN_METEO_MODEL` | Open-Meteoのmodels値 | `jma_gsm` |
| `DRY_RUN` | Discordへ送らず本文を標準出力へ表示するか | `false` |

## ローカル実行

```bash
uv sync
uv run python -m scripts.gigo_rain_discord update-stores --output data/gigo_stores.csv
uv run python -m scripts.gigo_rain_discord notify-weekly --source open_meteo_single_run --forecast-start-date 2026-06-09 --open-meteo-run 2026-06-07T00:00 --top-n-per-day 10 --dry-run
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..." uv run python -m scripts.gigo_rain_discord notify-weekly --source both --forecast-start-date 2026-06-09 --open-meteo-run 2026-06-07T00:00
```

## 注意点

Discordの通常メッセージ本文は2000文字までなので、長い出力は複数メッセージに分割します。

`jma_weekly` は、実行時点で気象庁が公開している最新の週間予報を送信する方式です。過去の日曜日に実行していなかった場合、その時点の気象庁公式予報をGitHub Actionsだけで後から復元することはできません。後から日曜時点を再現する用途は、Open-Meteo Single Runsの `open_meteo_run` 指定を使います。
