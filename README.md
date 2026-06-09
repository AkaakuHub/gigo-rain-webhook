# GiGO翌朝雨確率 Discord通知

GitHub Actionsで毎日1回、静的CSVに保存したGiGO店舗の座標を使って翌朝の降水確率を取得し、Discord webhookへ文字列で送信します。

Discordに表示される内容は次の形式です。

```text
GiGO調布/東京都 75%
GiGO町田/東京都 40%
GiGO府中/東京都 20%
```

Discord webhookのAPI仕様上、HTTPリクエストはPOSTで送り、本文は`content`に入れます。Discordに表示されるのは`content`の文字列だけです。

## 構成

```text
.github/workflows/gigo-rain-discord.yml      # 毎日の通知。スクレイピングしない
.github/workflows/update-gigo-stores.yml    # 手動の店舗CSV更新。初回と店舗追加時だけ使う
scripts/gigo_rain_discord.py                # 店舗CSV更新、天気取得、Discord送信
data/gigo_stores.csv                        # 静的店舗CSV。初回更新Actionで埋める
pyproject.toml
uv.lock
```

## 初回設定

1. このリポジトリをGitHubへpushします。
2. GitHubリポジトリの `Settings` → `Secrets and variables` → `Actions` → `New repository secret` で、Repository secretとして `DISCORD_WEBHOOK_URL` を登録します。
3. `Actions` タブから `Update static GiGO store CSV` を手動実行します。
4. `data/gigo_stores.csv` に店舗名、都道府県、住所、緯度、経度が書き込まれてコミットされます。
5. 以後は `Send GiGO rain ranking to Discord` が毎日21:17 JSTに動きます。

## 毎日実行で行うこと

処理は次の3つだけです。

1. `data/gigo_stores.csv` を読む。
2. Open-Meteoから翌日06:00〜12:00 JSTの `precipitation_probability` を取得する。
3. 降水確率が高い順に並べた文字列をDiscord webhookへ送信する。

## 店舗更新で行うこと

`Update static GiGO store CSV` は手動実行だけです。

このActionはGiGO公式店舗検索を47都道府県分読み、公式ページの検索結果件数と取得件数が一致しない場合は失敗します。住所から座標を取得し、`data/gigo_stores.csv` を更新します。既存CSVに同じ店舗IDと同じ住所の座標がある場合、またはGitHub Actionsの住所キャッシュに同じ住所の座標がある場合は、座標取得を再実行せずに再利用します。

手動実行時の `store_name_regex` は既定で `^GiGO` です。GiGO公式店舗検索に載っている全ブランドをCSVへ入れる場合は `.*` を指定します。

## 環境変数

| 変数名 | 内容 | 既定値 |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | Discord webhook URL。GitHub Secretsに登録します。 | なし |
| `GIGO_STORES_CSV` | 静的店舗CSVのパス | `data/gigo_stores.csv` |
| `TARGET_DAYS_AHEAD` | 何日後を対象にするか。翌朝なら1です。 | `1` |
| `MORNING_START_HOUR` | 朝の判定開始時刻。JSTの時です。 | `6` |
| `MORNING_END_HOUR` | 朝の判定終了時刻。JSTの時です。この時刻は含めません。 | `12` |
| `MIN_POP_PERCENT` | Discordへ載せる最低降水確率です。全件出すなら0です。 | `0` |
| `TOP_N` | 上位N件だけ送る場合に指定します。 | `10` |
| `OPEN_METEO_BATCH_SIZE` | Open-Meteoへまとめて問い合わせる店舗数です。 | `50` |
| `STORE_NAME_REGEX` | CSVへ保存する店舗名の正規表現です。 | `^GiGO` |

## ローカル実行

```bash
uv sync
uv run python -m scripts.gigo_rain_discord update-stores --output data/gigo_stores.csv
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..." uv run python -m scripts.gigo_rain_discord notify
```

## 注意点

Discordの通常メッセージ本文は2000文字までなので、全店舗を送る場合は複数メッセージに分割します。

`data/gigo_stores.csv` が空のまま毎日実行Actionを動かすと失敗します。初回だけ `Update static GiGO store CSV` を実行してください。
