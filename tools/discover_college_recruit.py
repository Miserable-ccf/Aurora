"""探测公办大专官网的招聘栏目 URL。

用法：python3 discover_recruit.py <whitelist.csv> <out.tsv>
从每所学校官网首页抽取链接，对"招聘/人事处/人才"等关键词加权打分，
再进入候选页验证（统计页内"招聘"出现次数），输出最佳 entry_url。
"""
import csv
import re
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import urllib3

urllib3.disable_warnings()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

TEXT_SCORES = [("诚聘英才", 6), ("招聘", 6), ("人才引进", 5), ("公开选聘", 5), ("人事处", 5), ("选聘", 4), ("人事", 4), ("人才", 3), ("通知公告", 2), ("信息公开", 1)]
URL_SCORES = [("zhaopin", 3), ("recruit", 3), ("renshi", 2), ("rsc", 2), ("talent", 2)]


def fetch(url, timeout=15):
    resp = requests.get(url, headers=HEADERS, timeout=timeout, verify=False, allow_redirects=True)
    resp.raise_for_status()
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        match = re.search(r'charset=["\']?([\w-]+)', resp.text[:2000], re.I)
        resp.encoding = match.group(1) if match else "utf-8"
    return resp


def registrable(host):
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def score_link(text, href):
    score = sum(weight for keyword, weight in TEXT_SCORES if keyword in text)
    lowered = href.lower()
    score += sum(weight for keyword, weight in URL_SCORES if keyword in lowered)
    return score


def discover(name, domain, site_url):
    base = registrable(domain)
    home = None
    for url in (site_url, f"http://www.{domain}/", f"https://{domain}/", f"http://{domain}/"):
        try:
            home = fetch(url)
            break
        except Exception:
            continue
    if home is None:
        return {"name": name, "domain": domain, "entry_url": "", "score": -1, "note": "homepage unreachable"}
    candidates = []
    for href, raw in re.findall(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', home.text, re.I | re.S):
        text = re.sub(r"<[^>]+>", "", raw).strip()
        href = href.strip()
        if not text or href.startswith(("javascript:", "#", "mailto:")):
            continue
        absolute = urllib.parse.urljoin(home.url, href)
        if not absolute.startswith("http"):
            continue
        host = urllib.parse.urlparse(absolute).hostname or ""
        if registrable(host) != base and not host.endswith(".edu.cn"):
            continue
        score = score_link(text, absolute)
        if score >= 3:
            candidates.append((score, text[:24], absolute))
    candidates.sort(key=lambda item: -item[0])
    unique = []
    for item in candidates:
        if item[2] not in {u[2] for u in unique}:
            unique.append(item)
    best = None
    for score, text, url in unique[:4]:
        try:
            page = fetch(url, timeout=12)
        except Exception:
            continue
        hits = len(re.findall("招聘", page.text))
        total = score + min(hits, 10) * 0.5
        if best is None or total > best[0]:
            best = (total, text, url, hits)
    if best:
        return {"name": name, "domain": domain, "entry_url": best[2], "score": round(best[0], 1), "note": f"anchor={best[1]} hits={best[3]}"}
    if unique:
        return {"name": name, "domain": domain, "entry_url": unique[0][2], "score": unique[0][0], "note": f"unverified anchor={unique[0][1]}"}
    return {"name": name, "domain": domain, "entry_url": "", "score": 0, "note": "no candidate links"}


def main():
    whitelist_csv, output_tsv = sys.argv[1], sys.argv[2]
    with open(whitelist_csv, encoding="utf-8") as handle:
        schools = list(csv.DictReader(handle))
    results = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(discover, s["institution_name"], s["official_domain"], s["official_site_url"]): s for s in schools}
        for future in as_completed(futures):
            school = futures[future]
            results[school["institution_id"]] = future.result()
    with open(output_tsv, "w", encoding="utf-8") as handle:
        handle.write("institution_id\tname\tentry_url\tscore\tnote\n")
        for school in schools:
            row = results[school["institution_id"]]
            handle.write(f"{school['institution_id']}\t{row['name']}\t{row['entry_url']}\t{row['score']}\t{row['note']}\n")
            print(f"{row['name']}\t{row['entry_url']}\t{row['score']}\t{row['note']}")


if __name__ == "__main__":
    main()
