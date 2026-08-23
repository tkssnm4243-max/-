#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scrape_flyers.py
-----------------
「ホントク」アプリ用の、チラシ自動取得スクリプト。

やっていること（1日1回、GitHub Actionsから実行される想定）:
  1. 各店舗のトクバイ店舗ページから、現在掲載中のチラシ画像を1〜2枚だけ取得する
  2. 画像に対して日本語OCR（Tesseract）をかけて、テキストを読み取る
  3. カタログの商品名（と別名）が読み取れた場所の近くから、価格らしき数字を探す
  4. 読み取れた価格が常識的な範囲（カタログ基準価格の0.4〜2.5倍）に収まっているものだけを採用する
  5. 前回の today_prices.json とマージして書き出す（今回読み取れなかった項目は、
     前回のデータをそのまま残す＝1回の失敗でデータが消えないようにする）

注意点（正直に書いておく）:
  - これは無料のOCR（Tesseract）を使っている。チラシは写真・図案・様々なフォントが
    混在するデザインなので、読み取り精度は完璧ではない。誤読・取りこぼしは普通に起こる。
  - 個人利用・低頻度（1日1回、各店舗1〜2画像のみ）を前提にしている。サーバーに
    負荷をかけないよう、リクエスト間には必ずスリープを入れている。
  - 対象サイト（トクバイ）の利用規約の解釈はユーザー自身の判断に委ねられる。
    このスクリプトはその判断を代行するものではない。
"""

import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

try:
    import pytesseract
    from PIL import Image
except ImportError:
    pytesseract = None
    Image = None

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
STORES_CONFIG_PATH = HERE / "stores.json"
OUTPUT_PATH = REPO_ROOT / "today_prices.json"
IMG_CACHE_DIR = HERE / "_flyer_cache"

# 個人利用であることを明示するUser-Agent。連絡先を入れておくと、万一サイト側が
# 気づいた場合に問い合わせ先が分かって親切（ここはご自身のメールアドレス等に
# 書き換えることを推奨）。
USER_AGENT = "hontoku-personal-price-checker/1.0 (individual, non-commercial, low-frequency daily check)"

REQUEST_TIMEOUT = 15  # seconds
SLEEP_BETWEEN_REQUESTS = 2.5  # seconds — サーバーに負荷をかけないための間隔
MAX_LEAFLETS_PER_STORE = 2  # 1店舗あたり最大何枚のチラシ画像を見るか

LEAFLET_LINK_RE = re.compile(r"/leaflets/(\d+)")
# チラシ画像の実URL。/leaflets/{id} の {id} は画像ファイル名と一致するとは限らない
# （チェーン店は bargain_leaflets/{id}.jpg で一致するが、個人商店枠は
# bargain_office_leaflets/.../{別ID}.jpg など全く別のファイル名になる）。
# そのため、ページのimgタグに書かれている実際の画像URL(data-src)を直接拾う。
LEAFLET_IMAGE_SRC_RE = re.compile(
    r'data-src="(https://image\.tokubai\.co\.jp/images/bargain[a-z_]*leaflets/[^"]+)"'
)
PRICE_RE = re.compile(r"(?:¥|￥)?\s*(\d{2,4})\s*円?")


def jst_now_iso():
    jst = timezone(timedelta(hours=9))
    return datetime.now(jst).strftime("%Y-%m-%d %H:%M JST")


def load_config():
    with open(STORES_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_previous_output():
    if OUTPUT_PATH.exists():
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"fetchedAt": None, "prices": {}}


def fetch_store_page(session, url):
    r = session.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.text


def extract_leaflet_image_urls(html):
    urls = []
    for m in LEAFLET_IMAGE_SRC_RE.finditer(html):
        url = m.group(1)
        if url not in urls:
            urls.append(url)
    return urls[:MAX_LEAFLETS_PER_STORE]


def download_image(session, url, dest_path):
    r = session.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    dest_path.write_bytes(r.content)
    return dest_path


def normalize(text):
    # 全角/半角ゆれなどを吸収
    return unicodedata.normalize("NFKC", text)


def ocr_words(image_path):
    """
    Tesseractの単語単位の座標つき出力(image_to_data)を使う。
    チラシは「価格はでかい数字、商品名はすぐ近くの小さい文字」というレイアウトが多く、
    単純にOCRテキストを1本の文字列として繋げて「文字としての距離が近い方を選ぶ」やり方は、
    別の商品の価格を誤って拾いやすい（実際に試して確認済み）。
    そこで、各単語の画面上の座標（left/top/width/height）を使い、
    「商品名の単語ブロックと、２次元的な距離が一番近い価格」を選ぶ方式にしている。

    戻り値: [{"text": str, "cx": float, "cy": float, "conf": float}, ...]
    """
    if pytesseract is None:
        raise RuntimeError(
            "pytesseract/Pillow is not installed. `pip install -r requirements.txt` first."
        )
    img = Image.open(image_path)
    # チラシは段組み前提でない --psm 11 (sparse text: バラバラに散った文字を拾うモード)
    data = pytesseract.image_to_data(
        img, lang="jpn", config="--psm 11", output_type=pytesseract.Output.DICT
    )
    words = []
    n = len(data.get("text", []))
    for i in range(n):
        raw = (data["text"][i] or "").strip()
        if not raw:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        left, top = data["left"][i], data["top"][i]
        width, height = data["width"][i], data["height"][i]
        words.append({
            "text": normalize(raw),
            "cx": left + width / 2.0,
            "cy": top + height / 2.0,
            "conf": conf,
        })
    return words


def group_words_into_lines(words, y_tolerance=18):
    """
    バラバラの単語を、だいたい同じ高さ(y座標)にあるものは同じ「行」としてまとめる。
    商品名がOCRで複数トークンに分割される（例:「豚バラ」「薄切り」）ことがよくあるため、
    行単位でテキストを連結してから別名(alias)のマッチングをする方が拾いやすい。

    戻り値: [{"text": str, "cx": float, "cy": float}, ...]  行ごとの連結テキストと中心座標
    """
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: w["cy"])
    lines = []
    current = [sorted_words[0]]
    for w in sorted_words[1:]:
        if abs(w["cy"] - current[-1]["cy"]) <= y_tolerance:
            current.append(w)
        else:
            lines.append(current)
            current = [w]
    lines.append(current)

    result = []
    for line_words in lines:
        line_words_sorted = sorted(line_words, key=lambda w: w["cx"])
        text = "".join(w["text"] for w in line_words_sorted)
        cx = sum(w["cx"] for w in line_words_sorted) / len(line_words_sorted)
        cy = sum(w["cy"] for w in line_words_sorted) / len(line_words_sorted)
        result.append({"text": text, "cx": cx, "cy": cy})
    return result


def match_items_spatially(words, items):
    """
    座標つきOCR結果から、カタログ商品ごとに一番もっともらしい価格を探す。

    手順:
      1. 単語を行にグルーピングして連結テキストを作る（商品名のトークン分割対策）
      2. 各行テキストの中から、商品の別名(alias)を含む行を探す → 商品名の座標
      3. 単語レベルで価格らしきパターンにマッチするものを全部拾う → 価格の座標
      4. 商品名の座標に一番近い（ユークリッド距離）価格を採用する
      5. カタログ基準価格の0.4〜2.5倍から外れる値は除外する（誤読対策）

    戻り値: { item_id: {"price": int, "matched_alias": str} }  見つかった分だけ
    """
    lines = group_words_into_lines(words)

    price_points = []  # (price, cx, cy)
    for w in words:
        m = PRICE_RE.fullmatch(w["text"]) or PRICE_RE.search(w["text"])
        if not m:
            continue
        try:
            price = int(m.group(1))
        except ValueError:
            continue
        price_points.append((price, w["cx"], w["cy"]))

    results = {}
    for item in items:
        base = item["base"]
        best = None  # (price, dist2, alias)
        for alias in item["aliases"]:
            alias_n = normalize(alias)
            for line in lines:
                if alias_n not in line["text"]:
                    continue
                for price, px, py in price_points:
                    if not (base * 0.4 <= price <= base * 2.5):
                        continue  # 常識的な範囲外は誤読・無関係とみなして除外
                    dist2 = (px - line["cx"]) ** 2 + (py - line["cy"]) ** 2
                    if best is None or dist2 < best[1]:
                        best = (price, dist2, alias)
        if best:
            results[item["id"]] = {"price": best[0], "matched_alias": best[2]}
    return results


def process_store(session, store, items, log):
    store_id = store["id"]
    try:
        html = fetch_store_page(session, store["tokubai_url"])
    except Exception as e:
        log["errors"].append(f"{store_id}: 店舗ページ取得失敗 ({e})")
        return {}

    image_urls = extract_leaflet_image_urls(html)
    if not image_urls:
        log["errors"].append(f"{store_id}: チラシ画像リンクが見つからなかった")
        return {}

    IMG_CACHE_DIR.mkdir(exist_ok=True)
    store_results = {}
    for i, img_url in enumerate(image_urls):
        time.sleep(SLEEP_BETWEEN_REQUESTS)
        img_path = IMG_CACHE_DIR / f"{store_id}_{i}.jpg"
        try:
            download_image(session, img_url, img_path)
        except Exception as e:
            log["errors"].append(f"{store_id}/{img_url}: 画像ダウンロード失敗 ({e})")
            continue

        try:
            words = ocr_words(img_path)
        except Exception as e:
            log["errors"].append(f"{store_id}/{img_url}: OCR失敗 ({e})")
            continue

        matched = match_items_spatially(words, items)
        for item_id, info in matched.items():
            # 複数チラシ画像で同じ商品が見つかった場合は、最初に見つかった方を優先
            if item_id not in store_results:
                store_results[item_id] = info

    return store_results


def main():
    config = load_config()
    previous = load_previous_output()
    prev_prices = previous.get("prices", {})

    log = {"errors": [], "matched": [], "unmatched": []}

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    merged_prices = dict(prev_prices)  # 前回のデータをベースに、取れた分だけ上書きする

    for store in config["stores"]:
        store_id = store["id"]
        results = process_store(session, store, config["items"], log)

        matched_ids = set(results.keys())
        all_ids = {it["id"] for it in config["items"]}
        for item_id in matched_ids:
            key = f"{store_id}|{item_id}"
            info = results[item_id]
            merged_prices[key] = {
                "sale": True,
                "price": info["price"],
                "source": "ocr",
            }
            log["matched"].append(f"{key} = ¥{info['price']} (「{info['matched_alias']}」の近くで検出)")

        for item_id in all_ids - matched_ids:
            log["unmatched"].append(f"{store_id}|{item_id}")

        time.sleep(SLEEP_BETWEEN_REQUESTS)

    output = {
        "fetchedAt": jst_now_iso(),
        "prices": merged_prices,
        "_log": {
            "matched_count": len(log["matched"]),
            "unmatched_count": len(log["unmatched"]),
            "errors": log["errors"],
        },
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # GitHub Actions のログにサマリーを出す（後で見返せるように）
    print(f"=== {jst_now_iso()} 実行結果 ===")
    print(f"マッチ件数: {len(log['matched'])}")
    for line in log["matched"]:
        print("  OK  " + line)
    print(f"未マッチ件数: {len(log['unmatched'])}")
    if log["errors"]:
        print("エラー:")
        for e in log["errors"]:
            print("  !! " + e)

    return 0


if __name__ == "__main__":
    sys.exit(main())
