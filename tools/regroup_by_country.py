#!/usr/bin/env python3
"""把 Gist 里 aggregator 生成的 clash.yaml 按国家重排，生成带国家分组的
clash-by-country.yaml，并回写到同一个 Gist。

默认用法（GitHub Actions 里跑）：
    python tools/regroup_by_country.py
    # 拉取 Gist 的 clash.yaml -> 按国家分组 -> PATCH 回同一 Gist
    # 需要环境变量 GIST_PAT（有 gist 权限的 GitHub token）

本地测试（不碰 Gist）：
    python tools/regroup_by_country.py --local in.yaml --out out.yaml
"""

import argparse
import json
import os
import sys
import urllib.request

import yaml

GIST_ID = "76731eb27ada95d0578df6dc9da1150b"
GIST_USER = "liliya888"
RAW_URL = f"https://gist.githubusercontent.com/{GIST_USER}/{GIST_ID}/raw/clash.yaml"
API_URL = f"https://api.github.com/gists/{GIST_ID}"
OUT_FILENAME = "clash-by-country.yaml"

# (分组名, 匹配关键词)，按顺序匹配，先命中为准；只匹配节点的 name 字段
GROUP_RULES = [
    ("🇸🇬 新加坡", ["🇸🇬", "新加坡", "SG-"]),
    ("🇭🇰 香港", ["🇭🇰", "香港", "HK-"]),
    ("🇺🇸 美国", ["🇺🇸", "美国", "USA", "US-"]),
    ("🇯🇵 日本", ["🇯🇵", "日本", "JP-"]),
    ("🇹🇼 台湾", ["🇹🇼", "台湾", "TW-"]),
    ("🇩🇪 德国", ["🇩🇪", "德国", "DE-"]),
    ("🇲🇾 马来西亚", ["🇲🇾", "马来西亚", "MYS"]),
    ("🇨🇳 中国", ["🇨🇳", "中国", "联通", "CN-"]),
    ("🇪🇺 欧洲", ["🇪🇺", "欧洲", "EUR"]),
]
OTHER_GROUP = "🌍 其他"


def country_of(name):
    hay = (name or "").upper()
    for group, keywords in GROUP_RULES:
        for kw in keywords:
            if kw.upper() in hay:
                return group
    return OTHER_GROUP


def regroup(data):
    """返回 (新配置 dict, [(分组名, 节点数), ...])。节点本身不动，只重排并加分组。"""
    proxies = data.get("proxies") or []
    order = [name for name, _ in GROUP_RULES] + [OTHER_GROUP]

    buckets = {}
    for p in proxies:
        buckets.setdefault(country_of(p.get("name")), []).append(p)

    # proxies 列表本身也按国家顺序重排，摊平成列表时同国节点挨在一起
    grouped_proxies = []
    for name in order:
        grouped_proxies.extend(buckets.get(name, []))
    all_names = [p.get("name") for p in grouped_proxies]

    country_groups = [
        {"name": name, "type": "select",
         "proxies": [p.get("name") for p in buckets[name]]}
        for name in order
        if buckets.get(name)
    ]
    country_names = [g["name"] for g in country_groups]

    new_data = dict(data)
    new_data["proxies"] = grouped_proxies

    # proxy-groups：保留原有分组（规则里引用的组一个不动），只改两处：
    # 1) "🔰 节点选择" 改成选国家分组；2) 在 "♻️ 自动选择" 后面插入国家分组
    orig_groups = list(data.get("proxy-groups") or [])
    group_names = {g.get("name") for g in orig_groups}
    new_groups = []
    inserted = False
    for g in orig_groups:
        g = dict(g)
        if g.get("name") == "🔰 节点选择":
            g["proxies"] = ["♻️ 自动选择", "🎯 全球直连"] + country_names
        elif g.get("name") == "♻️ 自动选择":
            g["proxies"] = all_names
        new_groups.append(g)
        if g.get("name") == "♻️ 自动选择" and not inserted:
            new_groups.extend(country_groups)
            inserted = True
    if not inserted:
        new_groups = country_groups + new_groups
    if "🔰 节点选择" not in group_names:
        new_groups.insert(0, {"name": "🔰 节点选择", "type": "select",
                              "proxies": ["♻️ 自动选择", "🎯 全球直连"] + country_names})
    if "♻️ 自动选择" not in group_names:
        new_groups.insert(1, {"name": "♻️ 自动选择", "type": "url-test",
                              "url": "http://www.gstatic.com/generate_204",
                              "interval": 300, "proxies": all_names})
    if "🎯 全球直连" not in group_names:
        new_groups.append({"name": "🎯 全球直连", "type": "direct"})
    new_data["proxy-groups"] = new_groups

    summary = [(name, len(buckets.get(name, []))) for name in order
               if buckets.get(name)]
    return new_data, summary


def fetch_clash_yaml():
    req = urllib.request.Request(
        RAW_URL, headers={"User-Agent": "aggregator-regroup/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def update_gist(token, filename, content):
    body = json.dumps({"files": {filename: {"content": content}}}).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, method="PATCH")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", default=None, help="本地输入文件（测试用，不碰 Gist）")
    ap.add_argument("--out", default=None, help="本地输出文件")
    args = ap.parse_args()

    if args.local:
        with open(args.local, encoding="utf-8") as f:
            text = f.read()
    else:
        text = fetch_clash_yaml()

    data = yaml.safe_load(text)
    if not isinstance(data, dict) or not data.get("proxies"):
        print("clash.yaml 里没有 proxies，退出", file=sys.stderr)
        return 1

    new_data, summary = regroup(data)
    out_text = yaml.dump(new_data, allow_unicode=True, sort_keys=False,
                         width=10 ** 6)

    if args.local:
        out_path = args.out or OUT_FILENAME
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(out_text)
        print(f"已写入 {out_path}")
    else:
        token = os.environ.get("GIST_PAT")
        if not token:
            print("缺少 GIST_PAT 环境变量", file=sys.stderr)
            return 1
        result = update_gist(token, OUT_FILENAME, out_text)
        raw = result.get("files", {}).get(OUT_FILENAME, {}).get("raw_url", "")
        print(f"Gist 已更新：{OUT_FILENAME}，raw_url={raw}")

    for name, count in summary:
        print(f"  {name}: {count}")
    print(f"  合计：{sum(c for _, c in summary)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
